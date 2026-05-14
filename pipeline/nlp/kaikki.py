"""
Kaikki-backed word-level lookup for any language pair.

This is the *production* copy of the lookup engine.  The canonical
development/evaluation version lives in eval/pipelines/ru_tr/kaikki_1/.

Architecture (ru→tr example):

    1. Direct lookup   — <src>_<tgt>.db  (kaikki.org dump, POS-filtered)
       ↓ if empty
    2. Lemmatize       — pymorphy3 normal_form, retry direct lookup
       ↓ if empty
    3. Two-hop         — <src>_en.db → en_<tgt>.db
                         <src>_de.db → de_<tgt>.db  (ranked by agreement)
       ↓ with OpenRussian EN-definition fallback for Russian sources

Usage in fill_word_translations.py:
    from nlp.kaikki import Lookup
    lk = Lookup("ru", "tr", db_path=Path("/path/to/ru_tr.db"))
    lk.lookup("победа")   # → ["zafer", "başarım", "galebe", "galibiyet"]
    lk.close()

The hop DBs (ru_en.db, ru_de.db, en_tr.db, de_tr.db for the ru→tr pair)
are expected to sit **in the same directory** as the primary DB file.
"""

from __future__ import annotations

import re
import sqlite3
import sys
import unicodedata
from pathlib import Path

# ---------------------------------------------------------------------------
# Repo-relative paths
# ---------------------------------------------------------------------------

_HERE = Path(__file__).parent          # pipeline/nlp/
_REPO_ROOT = _HERE.parents[1]          # repo root  (pipeline/nlp → pipeline → root)
_BACKEND_DIR = _REPO_ROOT / "backend"  # backend/  — needed for OpenRussian

# Default production DB location (under backend/dictionaries/).
# fill_word_translations.py always passes db_path explicitly, so this is
# only a fallback for ad-hoc REPL usage.
DEFAULT_DB = _BACKEND_DIR / "dictionaries" / "ru_tr" / "ru_tr.db"

# ---------------------------------------------------------------------------
# OpenRussian integration — optional, gracefully disabled when unavailable.
# ---------------------------------------------------------------------------

_or_loaded = False
_or_mod = None


def _ensure_or() -> object | None:
    global _or_loaded, _or_mod
    if _or_loaded:
        return _or_mod
    _or_loaded = True
    try:
        if str(_BACKEND_DIR) not in sys.path:
            sys.path.insert(0, str(_BACKEND_DIR))
        import openrussian as _or  # type: ignore
        _or.ensure_loaded()
        _or_mod = _or
    except Exception:
        _or_mod = None
    return _or_mod


def _or_en_tokens(key: str) -> list[str]:
    """Return English pivot tokens from OpenRussian for *key*."""
    mod = _ensure_or()
    if mod is None:
        return []
    try:
        defs = mod.lookup_all(key)
    except Exception:
        return []
    tokens: list[str] = []
    seen: set[str] = set()
    for phrase in defs:
        for part in phrase.split(","):
            t = part.strip().lower()
            if t and t not in seen:
                seen.add(t)
                tokens.append(t)
    return tokens


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_STRIP_CHARS = ".,!?;:—–-«»\"'"
_HOP_DB_NAMES = ["ru_en", "ru_de", "en_tr", "de_tr"]

# Map pymorphy3 POS tags to the kaikki DB pos values.
_PYMORPHY_TO_POS: dict[str, str] = {
    "NOUN": "noun",
    "ADJF": "adj",  "ADJS": "adj",  "PRTF": "adj",  "PRTS": "adj",
    "INFN": "verb", "VERB": "verb",
    "ADVB": "adv",
    "NPRO": "pron",
    "NUMR": "num",  "NUMB": "num",
    "PREP": "prep",
    "CONJ": "conj",
    "PRCL": "particle",
    "INTJ": "intj",
}


def _strip_stress(text: str) -> str:
    """Remove combining acute accent (U+0301) and normalise ё→е."""
    nfd = unicodedata.normalize("NFD", text)
    stripped = "".join(c for c in nfd if c != "\u0301")
    return unicodedata.normalize("NFC", stripped).replace("ё", "е").replace("Ё", "Е")


def _normalise(lemma: str) -> str:
    return _strip_stress(lemma).lower().strip(_STRIP_CHARS)


# ---------------------------------------------------------------------------
# Lookup class
# ---------------------------------------------------------------------------

class Lookup:
    """
    Read-only kaikki-backed lookup for a language pair.

    Parameters
    ----------
    src : str
        ISO 639-1 source language code (e.g. "ru").
    tgt : str
        ISO 639-1 target language code (e.g. "tr").
    db_path : Path
        Path to the primary <src>_<tgt>.db file.  Hop DBs are resolved
        from the same directory automatically.
    """

    _MAX_RESULTS = 4

    def __init__(self, src: str, tgt: str, db_path: Path = DEFAULT_DB) -> None:
        if not db_path.exists():
            raise FileNotFoundError(
                f"Kaikki DB not found at {db_path}.\n"
                "Build it first:\n"
                "  python -m eval.pipelines.ru_tr.kaikki_1.build_db --download\n"
                "Then copy/symlink it to the location above."
            )
        self._conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        self._conn.row_factory = sqlite3.Row

        # Hop DBs live alongside the primary DB (same directory).
        self._hop: dict[str, sqlite3.Connection] = {}
        for name in _HOP_DB_NAMES:
            p = db_path.parent / f"{name}.db"
            if p.exists():
                c = sqlite3.connect(f"file:{p}?mode=ro", uri=True)
                c.row_factory = sqlite3.Row
                self._hop[name] = c

        # pymorphy3 is only needed for Russian source language.
        self._morph = None
        if src == "ru":
            try:
                import pymorphy3
                self._morph = pymorphy3.MorphAnalyzer(lang="ru")
            except ImportError:
                pass

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _lemmatize(self, word: str) -> str:
        if self._morph is None:
            return word
        parses = self._morph.parse(word)
        return parses[0].normal_form if parses else word

    def _detect_pos(self, key: str) -> str | None:
        if self._morph is None:
            return None
        parses = self._morph.parse(key)
        if not parses:
            return None
        return _PYMORPHY_TO_POS.get(str(parses[0].tag.POS))

    @staticmethod
    def _clean(word: str) -> str:
        """Strip parenthetical/bracket annotations and trim."""
        word = re.sub(r"\s*\([^)]*\)", "", word)
        word = re.sub(r"\s*\[[^\]]*\]", "", word)
        return word.strip()

    @staticmethod
    def _filter_proper_nouns(words: list[str]) -> list[str]:
        """Remove multi-word results where any token starts with an uppercase letter."""
        filtered = [
            w for w in words
            if not (" " in w and any(t[0].isupper() for t in w.split() if t))
        ]
        return filtered if filtered else words

    @staticmethod
    def _tr_pos_filter(words: list[str], pos: str | None) -> list[str]:
        """Filter Turkish words by POS using morphological heuristics.

        Turkish verb infinitives end in -mak/-mek.
        """
        if pos == "verb":
            filtered = [w for w in words if w.endswith(("mak", "mek"))]
        elif pos == "noun":
            filtered = [w for w in words if not w.endswith(("mak", "mek"))]
        else:
            return words
        return filtered if filtered else words

    def _hop_words(self, db_name: str, lemma: str) -> list[str]:
        conn = self._hop.get(db_name)
        if not conn:
            return []
        rows = conn.execute(
            "SELECT DISTINCT tgt_word FROM translations WHERE lemma = ?", (lemma,)
        ).fetchall()
        return [c for r in rows if (c := self._clean(r["tgt_word"]))]

    def _two_hop(self, key: str, src_pos: str | None = None) -> list[str]:
        """ru→en→tr and ru→de→tr, ranked by agreement."""
        en_words = self._hop_words("ru_en", key)
        de_words = self._hop_words("ru_de", key)

        de_tr: set[str] = set()
        for w in de_words:
            de_tr.update(self._hop_words("de_tr", w.lower()))

        if de_tr:
            en_pivots = en_words
        else:
            en_pivots = en_words if src_pos else en_words[:1]

        en_tr: set[str] = set()
        for w in en_pivots:
            en_tr.update(self._hop_words("en_tr", w.lower()))

        or_tr: set[str] = set()
        for tok in _or_en_tokens(key):
            or_tr.update(self._hop_words("en_tr", tok))

        agreed  = sorted(en_tr & de_tr)
        de_only = sorted(de_tr - en_tr)
        en_only = sorted(en_tr - de_tr)
        or_only = sorted(or_tr - en_tr - de_tr)
        return agreed + de_only + en_only + or_only

    def _direct_lookup(self, key: str, pos: str | None) -> list[str]:
        """Fetch from the primary DB, preferring POS-filtered results."""
        if pos:
            rows = self._conn.execute(
                "SELECT DISTINCT tr_word FROM definitions WHERE lemma = ? AND pos = ?",
                (key, pos),
            ).fetchall()
            if rows:
                return [c for r in rows if (c := self._clean(r["tr_word"]))]
        rows = self._conn.execute(
            "SELECT DISTINCT tr_word FROM definitions WHERE lemma = ?",
            (key,),
        ).fetchall()
        return [c for r in rows if (c := self._clean(r["tr_word"]))]

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def lookup(self, lemma: str, grammar: str = "") -> list[str]:
        """Return up to _MAX_RESULTS Turkish translations for *lemma*."""
        key = _normalise(lemma)
        if not key:
            return []

        src_pos = self._detect_pos(key)
        normal = self._lemmatize(key)
        if src_pos is None and normal != key:
            src_pos = self._detect_pos(normal)

        # 1. Direct lookup (POS-filtered with unfiltered fallback)
        result = self._direct_lookup(key, src_pos)
        if result:
            return result[: self._MAX_RESULTS]

        # 2. Lemmatized retry
        if normal != key:
            result = self._direct_lookup(normal, src_pos)
            if result:
                return result[: self._MAX_RESULTS]

        # 3. Two-hop fallback
        for k in ([key, normal] if normal != key else [key]):
            result = self._two_hop(k, src_pos)
            if result:
                result = self._filter_proper_nouns(result)
                return self._tr_pos_filter(result, src_pos)[: self._MAX_RESULTS]

        return []

    def close(self) -> None:
        self._conn.close()
        for c in self._hop.values():
            c.close()
