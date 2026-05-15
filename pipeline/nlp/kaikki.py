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

# German personal / reflexive pronouns — closed class used for pron-pos filtering.
_GERMAN_PERSONAL_PRONOUNS: frozenset[str] = frozenset({
    "ich", "du", "er", "sie", "es", "wir", "ihr", "Sie",
    "man", "sich", "uns", "euch", "mich", "dich",
    "mir", "dir", "ihm", "ihnen", "Ihnen",
})

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
# Target-language POS filters (registered in _POS_FILTERS below the class)
# ---------------------------------------------------------------------------

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
                "Build it first: python -m eval.pipelines.{src}_{tgt}.kaikki_1.build_db\n"
                "Then copy/symlink it to the location above."
            )
        self._src = src
        self._tgt = tgt
        self._tgt_col = f"{tgt}_word"

        self._conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        self._conn.row_factory = sqlite3.Row

        # Discover hop pairs automatically: any {src}_{pivot}.db + {pivot}_{tgt}.db
        # found alongside the primary DB.  Works for any language pair.
        db_dir = db_path.parent
        self._hop_pairs: dict[str, tuple[sqlite3.Connection, sqlite3.Connection]] = {}
        for sp_path in sorted(db_dir.glob(f"{src}_*.db")):
            pivot = sp_path.stem[len(src) + 1:]
            if pivot == tgt:
                continue  # that is the primary DB itself
            pt_path = db_dir / f"{pivot}_{tgt}.db"
            if pt_path.exists():
                sp_conn = sqlite3.connect(f"file:{sp_path}?mode=ro", uri=True)
                sp_conn.row_factory = sqlite3.Row
                pt_conn = sqlite3.connect(f"file:{pt_path}?mode=ro", uri=True)
                pt_conn.row_factory = sqlite3.Row
                self._hop_pairs[pivot] = (sp_conn, pt_conn)

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
        """Strip parenthetical/bracket annotations and trim.

        Also collapses slash-separated inflectional variants (Wiktionary
        convention, e.g. 'laß/lasse/lassen') to the longest single form.
        """
        word = re.sub(r"\s*\([^)]*\)", "", word)
        word = re.sub(r"\s*\[[^\]]*\]", "", word)
        word = word.strip()
        if "/" in word:
            parts = [p.strip() for p in word.split("/") if p.strip()]
            if parts:
                word = max(parts, key=len)
        return word

    @staticmethod
    def _alternate_spellings(lemma: str) -> list[str]:
        """Return alternative Russian spellings when primary lookup fails.

        pymorphy3 sometimes produces archaic forms while ruwiktionary uses the
        modern spelling (e.g. счастие → счастье).  Swap -ие/-ье both ways.
        """
        if lemma.endswith("ие"):
            return [lemma[:-2] + "ье"]
        if lemma.endswith("ья"):
            return [lemma[:-2] + "ия"]
        return []

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
        """Filter Turkish words by POS. Verb infinitives end in -mak/-mek."""
        if pos == "verb":
            filtered = [w for w in words if w.endswith(("mak", "mek"))]
        elif pos == "noun":
            filtered = [w for w in words if not w.endswith(("mak", "mek"))]
        else:
            return words
        return filtered if filtered else words

    @staticmethod
    def _de_pos_filter(words: list[str], pos: str | None) -> list[str]:
        """Filter German words by POS using German-specific rules.

        Nouns are always capitalised. Verb infinitives end in -en/-eln/-ern
        (len >= 4) or start with 'sich '. Pronouns are matched against the
        closed-class personal pronoun set. Safe fallback: returns the original
        list when filtering would leave nothing.
        """
        if pos == "noun":
            filtered = [w for w in words if w and w[0].isupper()]
        elif pos == "verb":
            filtered = [
                w for w in words
                if w and (
                    (w.lower().endswith(("en", "eln", "ern")) and len(w) >= 4)
                    or w.lower().startswith("sich ")
                )
            ]
        elif pos in ("adj", "adv"):
            filtered = [w for w in words if w and w[0].islower()]
        elif pos == "pron":
            filtered = [w for w in words if w.lower() in _GERMAN_PERSONAL_PRONOUNS
                        or w in _GERMAN_PERSONAL_PRONOUNS]
        else:
            return words
        return filtered if filtered else words

    def _tgt_pos_filter(self, words: list[str], pos: str | None) -> list[str]:
        """Dispatch to the target-language POS filter."""
        fn = _POS_FILTERS.get(self._tgt)
        return fn(words, pos) if fn else words

    def _hop_words_conn(self, conn: sqlite3.Connection, lemma: str) -> list[str]:
        """Fetch and clean tgt_word results from a hop DB connection."""
        rows = conn.execute(
            "SELECT DISTINCT tgt_word FROM translations WHERE lemma = ?", (lemma,)
        ).fetchall()
        seen: set[str] = set()
        result: list[str] = []
        for r in rows:
            c = self._clean(r["tgt_word"])
            if c and c not in seen:
                seen.add(c)
                result.append(c)
        return result

    def _two_hop(self, key: str, src_pos: str | None = None) -> list[str]:
        """Generic multi-pivot two-hop: {src}→{pivot}→{tgt} for each discovered pivot pair.

        When multiple pivots are available (e.g. en and de for ru→tr), words
        that appear via multiple pivots are ranked first (agreement signal).
        An OpenRussian bonus is added for Russian source with an English pivot.
        """
        if not self._hop_pairs:
            return []

        pivot_tgt_sets: dict[str, set[str]] = {}
        for pivot, (sp_conn, pt_conn) in self._hop_pairs.items():
            pivot_words = self._hop_words_conn(sp_conn, key)
            tgt_set: set[str] = set()
            for w in pivot_words:
                for tw in self._hop_words_conn(pt_conn, w.lower()):
                    tgt_set.add(tw)
            if tgt_set:
                pivot_tgt_sets[pivot] = tgt_set

        # OpenRussian bonus: use English definition tokens as extra EN pivot words.
        or_bonus: set[str] = set()
        if self._src == "ru" and "en" in self._hop_pairs:
            _, pt_conn = self._hop_pairs["en"]
            for tok in _or_en_tokens(key):
                for tw in self._hop_words_conn(pt_conn, tok):
                    or_bonus.add(tw)

        if not pivot_tgt_sets and not or_bonus:
            return []

        all_sets = list(pivot_tgt_sets.values())
        if not all_sets:
            return sorted(or_bonus)
        if len(all_sets) == 1:
            main = all_sets[0]
            return sorted(main) + sorted(or_bonus - main)

        # Multiple pivots: agreed words first, then remainder, then OR-only.
        agreed = set.intersection(*all_sets)
        all_union = set.union(*all_sets)
        or_only = or_bonus - all_union
        return sorted(agreed) + sorted(all_union - agreed) + sorted(or_only)

    def _direct_lookup(self, key: str, pos: str | None) -> list[str]:
        """Fetch from the primary DB, preferring POS-filtered results."""
        col = self._tgt_col

        def _dedup(rows) -> list[str]:
            seen: set[str] = set()
            result: list[str] = []
            for r in rows:
                c = self._clean(r[col])
                if c and c not in seen:
                    seen.add(c)
                    result.append(c)
            return result

        if pos:
            rows = self._conn.execute(
                f"SELECT DISTINCT {col} FROM definitions WHERE lemma = ? AND pos = ?",
                (key, pos),
            ).fetchall()
            if rows:
                return _dedup(rows)
        rows = self._conn.execute(
            f"SELECT DISTINCT {col} FROM definitions WHERE lemma = ?",
            (key,),
        ).fetchall()
        return _dedup(rows)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def lookup(self, lemma: str, grammar: str = "") -> list[str]:
        """Return up to _MAX_RESULTS target-language translations for *lemma*."""
        key = _normalise(lemma)
        if not key:
            return []

        src_pos = self._detect_pos(key)
        normal = self._lemmatize(key)
        if src_pos is None and normal != key:
            src_pos = self._detect_pos(normal)

        # 1. Direct lookup (SQL POS-filtered + target-language Python filter)
        result = self._tgt_pos_filter(self._direct_lookup(key, src_pos), src_pos)
        if result:
            return result[: self._MAX_RESULTS]

        # 2. Lemmatized retry
        if normal != key:
            result = self._tgt_pos_filter(self._direct_lookup(normal, src_pos), src_pos)
            if result:
                return result[: self._MAX_RESULTS]

        # 3. Alternate spelling variants (Russian source: -ие/-ье swap)
        if self._src == "ru":
            for cand in self._alternate_spellings(key) + self._alternate_spellings(normal):
                result = self._tgt_pos_filter(self._direct_lookup(cand, src_pos), src_pos)
                if result:
                    return result[: self._MAX_RESULTS]

        # 4. Two-hop fallback
        for k in ([key, normal] if normal != key else [key]):
            result = self._two_hop(k, src_pos)
            if result:
                result = self._filter_proper_nouns(result)
                return self._tgt_pos_filter(result, src_pos)[: self._MAX_RESULTS]

        return []

    def close(self) -> None:
        self._conn.close()
        for sp_conn, pt_conn in self._hop_pairs.values():
            sp_conn.close()
            pt_conn.close()


# ---------------------------------------------------------------------------
# POS filter registry — maps target language code to its filter function.
# Add an entry here when adding a new language pair.
# ---------------------------------------------------------------------------

_POS_FILTERS: dict[str, object] = {
    "tr": Lookup._tr_pos_filter,
    "de": Lookup._de_pos_filter,
}
