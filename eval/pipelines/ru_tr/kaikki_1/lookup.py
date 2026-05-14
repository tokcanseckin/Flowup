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
import unicodedata
from pathlib import Path

_HERE = Path(__file__).parent
DEFAULT_DB = _HERE / "data" / "ru_tr.db"

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

    def _hop_words(self, db_name: str, lemma: str) -> list[str]:
        conn = self._hop.get(db_name)
        if not conn:
            return []
        rows = conn.execute(
            "SELECT DISTINCT tgt_word FROM translations WHERE lemma = ?", (lemma,)
        ).fetchall()
        return [r["tgt_word"] for r in rows]

    def _two_hop(self, key: str) -> list[str]:
        """ru→en→tr and ru→de→tr, returning intersection when both agree, else union."""
        en_words = self._hop_words("ru_en", key)
        de_words = self._hop_words("ru_de", key)

        en_tr: set[str] = set()
        for w in en_words:
            en_tr.update(self._hop_words("en_tr", w.lower()))

        de_tr: set[str] = set()
        for w in de_words:
            de_tr.update(self._hop_words("de_tr", w.lower()))

        joint = en_tr & de_tr
        if joint:
            return sorted(joint)
        combined = en_tr | de_tr
        return sorted(combined)

    def lookup(self, lemma: str, grammar: str = "") -> list[str]:
        key = _normalise(lemma)
        if not key:
            return []

        # 1. Direct ru→tr lookup
        rows = self._conn.execute(
            "SELECT DISTINCT tr_word FROM definitions WHERE lemma = ?",
            (key,),
        ).fetchall()
        if rows:
            return [row["tr_word"] for row in rows]

        # 2. Lemmatize and retry direct
        normal = self._lemmatize(key)
        if normal != key:
            rows = self._conn.execute(
                "SELECT DISTINCT tr_word FROM definitions WHERE lemma = ?",
                (normal,),
            ).fetchall()
            if rows:
                return [row["tr_word"] for row in rows]

        # 3. Two-hop fallback (ru→en→tr and ru→de→tr)
        for k in ([key, normal] if normal != key else [key]):
            result = self._two_hop(k)
            if result:
                return result

        return []

    def close(self) -> None:
        self._conn.close()
        for c in self._hop.values():
            c.close()
