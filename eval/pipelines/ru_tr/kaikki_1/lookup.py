"""
ru→tr lookup backed by the local kaikki_1 SQLite DB.

Uses pymorphy3 to reduce inflected word forms to their dictionary headword
(normal_form) before lookup, so e.g. "бесконечная" → "бесконечный".

Build the DB first:
    python -m eval.pipelines.ru_tr.kaikki_1.build_db --download

Then run eval:
    python -m eval.run --pipeline ru_tr/kaikki_1
"""

from __future__ import annotations

import sqlite3
import sys
import unicodedata
from pathlib import Path

_HERE = Path(__file__).parent

# ---------------------------------------------------------------------------
# OpenRussian integration — optional, gracefully disabled when unavailable.
# The backend/ directory contains openrussian.py which provides a
# Russian → English definition lookup backed by a local CSV cache.
# ---------------------------------------------------------------------------
_BACKEND_DIR = _HERE.parents[3] / "backend"
_or_loaded = False
_or_mod = None


def _ensure_or() -> object | None:
    """Lazily load the openrussian module (once per process)."""
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
    """Return English pivot tokens from OpenRussian for *key*.

    OR definitions are comma-separated entries like 'happiness, luck, good fortune'.
    Each comma-separated segment is used as a single lookup key — multi-word
    phrases such as 'good fortune' are passed through whole rather than split
    word-by-word, so they look up as one unit in en_tr.db (and return nothing
    if there is no exact match, avoiding spurious partial-word hits).
    """
    mod = _ensure_or()
    if mod is None:
        return []
    try:
        defs = mod.lookup_all(key)  # list[str]
    except Exception:
        return []
    tokens: list[str] = []
    seen: set[str] = set()
    for phrase in defs:
        for part in phrase.split(','):
            t = part.strip().lower()
            if t and t not in seen:
                seen.add(t)
                tokens.append(t)
    return tokens
DEFAULT_DB = _HERE / "data" / "ru_tr.db"

# Map pymorphy3 POS tags to the kaikki DB pos values.
_PYMORPHY_TO_POS: dict[str, str] = {
    "NOUN": "noun",
    "ADJF": "adj", "ADJS": "adj", "PRTF": "adj", "PRTS": "adj",
    "INFN": "verb", "VERB": "verb",
    "ADVB": "adv",
    "NPRO": "pron",
    "NUMR": "num", "NUMB": "num",
    "PREP": "prep",
    "CONJ": "conj",
    "PRCL": "particle",
    "INTJ": "intj",
}

_HOP_DB_NAMES = ["ru_en", "ru_de", "en_tr", "de_tr"]

_STRIP_CHARS = ".,!?;:—–-«»\"'"


def _strip_stress(text: str) -> str:
    # Only remove the combining acute accent (U+0301) used for Russian stress marks.
    # Normalise ё → е to match what build_db.py stores in the DB.
    nfd = unicodedata.normalize("NFD", text)
    stripped = "".join(c for c in nfd if c != "\u0301")
    return unicodedata.normalize("NFC", stripped).replace("ё", "е").replace("Ё", "Е")


def _normalise(lemma: str) -> str:
    return _strip_stress(lemma).lower().strip(_STRIP_CHARS)


class Lookup:
    def __init__(self, src: str, tgt: str, db_path: Path = DEFAULT_DB) -> None:
        if not db_path.exists():
            raise FileNotFoundError(
                f"kaikki_1 DB not found at {db_path}.\n"
                "Build it first:\n"
                "  python -m eval.pipelines.ru_tr.kaikki_1.build_db --download"
            )
        self._conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        self._conn.row_factory = sqlite3.Row

        # Open hop DBs lazily — they're optional (missing = two-hop disabled)
        self._hop: dict[str, sqlite3.Connection] = {}
        for name in _HOP_DB_NAMES:
            p = db_path.parent / f"{name}.db"
            if p.exists():
                c = sqlite3.connect(f"file:{p}?mode=ro", uri=True)
                c.row_factory = sqlite3.Row
                self._hop[name] = c

        import pymorphy3
        self._morph = pymorphy3.MorphAnalyzer(lang="ru")

    def _lemmatize(self, word: str) -> str:
        """Return the pymorphy3 normal_form (dictionary headword) for *word*."""
        parses = self._morph.parse(word)
        if not parses:
            return word
        return parses[0].normal_form

    def _detect_pos(self, key: str) -> str | None:
        """Return kaikki-style POS for *key* using pymorphy3, or None."""
        parses = self._morph.parse(key)
        if not parses:
            return None
        return _PYMORPHY_TO_POS.get(str(parses[0].tag.POS))

    @staticmethod
    def _tr_pos_filter(words: list[str], pos: str | None) -> list[str]:
        """Filter Turkish words by POS using morphological heuristics.

        Turkish verb infinitives end in -mak/-mek.  For a verb source we keep
        only those; for a noun source we exclude them.  If filtering would
        empty the list, the original list is returned unchanged (safe fallback).
        """
        if pos == "verb":
            filtered = [w for w in words if w.endswith(("mak", "mek"))]
        elif pos == "noun":
            filtered = [w for w in words if not w.endswith(("mak", "mek"))]
        else:
            return words
        return filtered if filtered else words

    @staticmethod
    def _clean(word: str) -> str:
        """Strip parenthetical/bracket annotations like '(veraltend)' or '[4, 5]' and trim."""
        import re as _re
        word = _re.sub(r'\s*\([^)]*\)', '', word)
        word = _re.sub(r'\s*\[[^\]]*\]', '', word)
        return word.strip()

    @staticmethod
    def _filter_proper_nouns(words: list[str]) -> list[str]:
        """Remove multi-word proper nouns (e.g. 'Fetih Suresi') from candidates.

        Heuristic: a result is treated as a proper noun when it contains at
        least one space *and* any of its tokens starts with an uppercase letter.
        Single-token capitalized words are left in — they are harder to
        distinguish from abbreviations or sentence-initial capitalisation.
        """
        filtered = [
            w for w in words
            if not (' ' in w and any(t[0].isupper() for t in w.split() if t))
        ]
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
        """ru→en→tr and ru→de→tr, ranked by agreement.

        Items appearing in *both* EN and DE paths are returned first (most
        reliable).  DE-only items come next (German→Turkish alignment tends to
        be cleaner for Slavic concepts), EN-only items last.  Returns [] only
        when both paths are completely empty.
        """
        en_words = self._hop_words("ru_en", key)
        de_words = self._hop_words("ru_de", key)

        de_tr: set[str] = set()
        for w in de_words:
            de_tr.update(self._hop_words("de_tr", w.lower()))

        # When the DE path yields results, use all EN pivots (DE acts as a
        # quality filter via the intersection/ranking step below).
        # When the DE path is empty we have no cross-validation, so restrict
        # EN pivots to the first word only (primary sense), reducing noise
        # from semantically drifted secondary/tertiary translations.
        if de_tr:
            en_pivots = en_words
        else:
            # When no DE cross-validation: if POS is known, use all EN pivots
            # and rely on POS filtering to handle noise; otherwise restrict
            # to the primary sense only.
            en_pivots = en_words if src_pos else en_words[:1]

        en_tr: set[str] = set()
        for w in en_pivots:
            en_tr.update(self._hop_words("en_tr", w.lower()))

        # OpenRussian fallback — look up the Russian key in the OR English
        # definitions, then hop each EN token through en_tr.db.
        # Results land in a separate set ranked below kaikki and DE paths.
        or_tr: set[str] = set()
        for tok in _or_en_tokens(key):
            or_tr.update(self._hop_words("en_tr", tok))

        agreed  = sorted(en_tr & de_tr)
        de_only = sorted(de_tr - en_tr)
        en_only = sorted(en_tr - de_tr)
        or_only = sorted(or_tr - en_tr - de_tr)
        return agreed + de_only + en_only + or_only

    def _direct_lookup(self, key: str, pos: str | None) -> list[str]:
        """Fetch translations from ru_tr.db, preferring *pos*-filtered results.

        When *pos* is known the query is first tried with ``AND pos = ?``.  If
        that returns nothing, the unfiltered query is used as a fallback so we
        never miss a word just because its POS tag in the DB differs slightly.
        """
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

    _MAX_RESULTS = 4

    def lookup(self, lemma: str, grammar: str = "") -> list[str]:
        key = _normalise(lemma)
        if not key:
            return []

        # Detect source POS once; used for filtering in all three paths below.
        src_pos = self._detect_pos(key)
        normal = self._lemmatize(key)
        if src_pos is None and normal != key:
            src_pos = self._detect_pos(normal)

        # 1. Direct ru→tr lookup (POS-filtered, with fallback)
        result = self._direct_lookup(key, src_pos)
        if result:
            return result[:self._MAX_RESULTS]

        # 2. Lemmatize and retry direct
        if normal != key:
            result = self._direct_lookup(normal, src_pos)
            if result:
                return result[:self._MAX_RESULTS]

        # 3. Two-hop fallback (ru→en→tr and ru→de→tr)
        for k in ([key, normal] if normal != key else [key]):
            result = self._two_hop(k, src_pos)
            if result:
                result = self._filter_proper_nouns(result)
                return self._tr_pos_filter(result, src_pos)[:self._MAX_RESULTS]

        return []

    def close(self) -> None:
        self._conn.close()
        for c in self._hop.values():
            c.close()
