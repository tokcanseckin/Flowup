"""
ru→de lookup backed by the local kaikki_1 SQLite DB.

Uses pymorphy3 to reduce inflected word forms to their dictionary headword
(normal_form) before lookup, so e.g. "бесконечная" → "бесконечный".

Build the DB first:
    python -m eval.pipelines.ru_de.kaikki_1.build_db

Then run eval:
    python -m eval.run --pipeline ru_de/kaikki_1 --tgt de
"""

from __future__ import annotations

import re
import sqlite3
import unicodedata
from pathlib import Path

_HERE = Path(__file__).parent

DEFAULT_DB = _HERE / "data" / "ru_de.db"

# Hop DB paths (built via build_db.py --build-hops)
_RU_EN_DB = _HERE / "data" / "ru_en.db"
_EN_DE_DB = _HERE / "data" / "en_de.db"

_HOP_DB_NAMES = ["ru_en", "en_de"]

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

_STRIP_CHARS = ".,!?;:—–-«»\"'"

# Closed set of German personal / reflexive pronouns.
# Used to filter results when the Russian source word is a pronoun,
# since ruwiktionary often stores pronoun entries under pos='noun'.
_GERMAN_PERSONAL_PRONOUNS: frozenset[str] = frozenset({
    "ich", "du", "er", "sie", "es", "wir", "ihr", "Sie",
    "man", "sich", "uns", "euch", "mich", "dich",
    "mir", "dir", "ihm", "ihr", "ihnen", "Ihnen",
})


def _strip_stress(text: str) -> str:
    """Remove combining acute accent (U+0301) used for Russian stress marks.

    Normalises ё → е to match what build_db.py stores in the DB.
    """
    nfd = unicodedata.normalize("NFD", text)
    stripped = "".join(c for c in nfd if c != "\u0301")
    return unicodedata.normalize("NFC", stripped).replace("ё", "е").replace("Ё", "Е")


def _normalise(lemma: str) -> str:
    return _strip_stress(lemma).lower().strip(_STRIP_CHARS)


class Lookup:
    def __init__(self, src: str, tgt: str, db_path: Path = DEFAULT_DB) -> None:
        if not db_path.exists():
            raise FileNotFoundError(
                f"kaikki_1 ru→de DB not found at {db_path}.\n"
                "Build it first:\n"
                "  python -m eval.pipelines.ru_de.kaikki_1.build_db"
            )
        self._conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        self._conn.row_factory = sqlite3.Row

        # Open hop DBs lazily — optional (missing = two-hop disabled)
        self._hop: dict[str, sqlite3.Connection] = {}
        for name, path in [("ru_en", _RU_EN_DB), ("en_de", _EN_DE_DB)]:
            if path.exists():
                c = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
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
    def _clean(word: str) -> str:
        """Strip parenthetical/bracket annotations like '(veraltet)' or '[4]' and trim.

        Also collapses slash-separated inflectional variants (Wiktionary convention,
        e.g. 'laß/lasse/lassen') to the longest single form ('lassen').
        """
        word = re.sub(r'\s*\([^)]*\)', '', word)
        word = re.sub(r'\s*\[[^\]]*\]', '', word)
        word = word.strip()
        if '/' in word:
            parts = [p.strip() for p in word.split('/') if p.strip()]
            if parts:
                word = max(parts, key=len)
        return word

    @staticmethod
    def _alternate_spellings(lemma: str) -> list[str]:
        """Return alternative Russian spellings to try when the primary lookup fails.

        pymorphy3 sometimes returns archaic forms where ruwiktionary uses the
        modern spelling (e.g. счастие → счастье).  We swap the -ие/-ье suffix
        in both directions as a safe fallback.
        """
        if lemma.endswith("ие"):
            return [lemma[:-2] + "ье"]
        if lemma.endswith("ья"):
            return [lemma[:-2] + "ия"]
        return []

    @staticmethod
    def _de_pos_filter(words: list[str], pos: str | None) -> list[str]:
        """Filter German candidate words by POS using German-specific rules.

        Nouns: always capitalised in German.
        Verbs: infinitives end in -en / -eln / -ern (length >= 4).
            Reflexive infinitives start with "sich ".
        Adjectives / adverbs: lowercase, no infinitive ending.
        Pronouns: matched against the closed-class personal pronoun set.

        Safe fallback: if filtering would empty the list, the original is
        returned unchanged.
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

    def _hop_words(self, db_name: str, lemma: str) -> list[str]:
        conn = self._hop.get(db_name)
        if not conn:
            return []
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
        """ru→en→de: look up key in ru_en.db, then each EN word in en_de.db.

        German POS filtering is applied via capitalisation heuristics:
        nouns start with uppercase; verbs/adj/adv are lowercase.
        The hop tables do not carry a pos column, so this is the only
        POS signal available at this stage.
        """
        en_words = self._hop_words("ru_en", key)
        if not en_words:
            return []
        de_words: list[str] = []
        seen: set[str] = set()
        for en in en_words:
            for de in self._hop_words("en_de", en.lower()):
                if de not in seen:
                    seen.add(de)
                    de_words.append(de)
        return self._de_pos_filter(de_words, src_pos)

    def _direct_lookup(self, key: str, pos: str | None) -> list[str]:
        """Fetch translations from ru_de.db, preferring *pos*-filtered results.

        When *pos* is known, the query is first tried with ``AND pos = ?``.
        Falls back to unfiltered query so we never miss a word due to POS mismatch.
        """
        def _dedup_clean(rows) -> list[str]:
            seen: set[str] = set()
            result: list[str] = []
            for r in rows:
                c = self._clean(r["de_word"])
                if c and c not in seen:
                    seen.add(c)
                    result.append(c)
            return result

        if pos:
            rows = self._conn.execute(
                "SELECT DISTINCT de_word FROM definitions WHERE lemma = ? AND pos = ?",
                (key, pos),
            ).fetchall()
            if rows:
                return _dedup_clean(rows)
        rows = self._conn.execute(
            "SELECT DISTINCT de_word FROM definitions WHERE lemma = ?",
            (key,),
        ).fetchall()
        return _dedup_clean(rows)

    _MAX_RESULTS = 4

    def lookup(self, lemma: str, grammar: str = "") -> list[str]:
        key = _normalise(lemma)
        if not key:
            return []

        # Detect source POS once; used for filtering in both paths below.
        src_pos = self._detect_pos(key)
        normal = self._lemmatize(key)
        if src_pos is None and normal != key:
            src_pos = self._detect_pos(normal)

        # 1. Direct ru→de lookup (POS-filtered, with fallback)
        result = self._de_pos_filter(self._direct_lookup(key, src_pos), src_pos)
        if result:
            return result[:self._MAX_RESULTS]

        # 2. Lemmatize and retry direct
        if normal != key:
            result = self._de_pos_filter(self._direct_lookup(normal, src_pos), src_pos)
            if result:
                return result[:self._MAX_RESULTS]

        # 3. Alternate spelling variants (e.g. pymorphy3 archaic -ие vs modern -ье)
        for candidate in (self._alternate_spellings(key) + self._alternate_spellings(normal)):
            result = self._de_pos_filter(self._direct_lookup(candidate, src_pos), src_pos)
            if result:
                return result[:self._MAX_RESULTS]

        # 4. Two-hop ru→en→de fallback
        for k in ([key, normal] if normal != key else [key]):
            result = self._two_hop(k, src_pos)
            if result:
                return result[:self._MAX_RESULTS]

        return []

    def close(self) -> None:
        self._conn.close()
        for c in self._hop.values():
            c.close()
