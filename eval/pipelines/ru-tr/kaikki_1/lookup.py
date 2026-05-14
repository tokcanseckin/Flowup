"""
ru→tr lookup backed by the local kaikki_1 SQLite DB.

Build the DB first:
    python -m eval.pipelines.ru_tr.kaikki_1.build_db --download

Then run eval:
    python -m eval.run --pipeline ru-tr/kaikki_1
"""

from __future__ import annotations

import sqlite3
import unicodedata
from pathlib import Path

_HERE = Path(__file__).parent
DEFAULT_DB = _HERE / "data" / "ru_tr.db"

_STRIP_CHARS = ".,!?;:—–-«»\"'"


def _strip_stress(text: str) -> str:
    return "".join(
        c for c in unicodedata.normalize("NFD", text)
        if unicodedata.category(c) != "Mn"
    )


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

    def lookup(self, lemma: str, grammar: str = "") -> list[str]:
        key = _normalise(lemma)
        if not key:
            return []

        rows = self._conn.execute(
            "SELECT DISTINCT tr_word FROM definitions WHERE lemma = ?",
            (key,),
        ).fetchall()

        return [row["tr_word"] for row in rows]

    def close(self) -> None:
        self._conn.close()
