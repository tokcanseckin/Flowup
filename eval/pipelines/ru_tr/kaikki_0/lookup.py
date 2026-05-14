"""
Lemma → Turkish definition lookup backed by the local kaikki SQLite DB.

Build the DB first:
    python -m eval.kaikki_db

Usage:
    from eval.lookup import LemmaLookup
    lk = LemmaLookup()
    lk.lookup("жить")    # → ["yaşamak (to live)", "olmak"]
    lk.lookup("жи́ть")   # stress marks stripped automatically
    lk.close()
"""

from __future__ import annotations

import sqlite3
import unicodedata
from pathlib import Path

DEFAULT_DB = Path(__file__).parent / "data" / "ru_tr.db"

# Punctuation/marks that may appear on the edges of a lemma from the API
_STRIP_CHARS = ".,!?;:—–-«»\"'"


def _strip_stress(text: str) -> str:
    """Remove Unicode combining marks (accent / stress) from text."""
    return "".join(
        c for c in unicodedata.normalize("NFD", text)
        if unicodedata.category(c) != "Mn"
    )


def _normalise(lemma: str) -> str:
    return _strip_stress(lemma).lower().strip(_STRIP_CHARS)


class LemmaLookup:
    """
    Read-only interface to the kaikki ru→tr SQLite DB.

    The DB stores plain (stress-stripped, lower-case) Russian lemmas.
    This class normalises incoming lemmas before lookup so stress marks
    from the API are handled transparently.
    """

    def __init__(self, db_path: Path = DEFAULT_DB) -> None:
        if not db_path.exists():
            raise FileNotFoundError(
                f"kaikki DB not found at {db_path}.\n"
                "Build it first:  python -m eval.kaikki_db"
            )
        # Read-only connection via URI
        self._conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        self._conn.row_factory = sqlite3.Row

    # ------------------------------------------------------------------
    def lookup(self, lemma: str) -> list[str]:
        """
        Return a de-duplicated list of Turkish translations for *lemma*.
        Returns an empty list if nothing is found.
        """
        key = _normalise(lemma)
        if not key:
            return []

        rows = self._conn.execute(
            "SELECT tr_word, tr_sense FROM definitions WHERE lemma = ?",
            (key,),
        ).fetchall()

        seen: set[str] = set()
        results: list[str] = []
        for row in rows:
            tr_word = row["tr_word"]
            tr_sense = row["tr_sense"]
            # Build "word (sense)" or just "word"
            entry = f"{tr_word} ({tr_sense})" if tr_sense else tr_word
            if entry not in seen:
                seen.add(entry)
                results.append(entry)

        return results

    # ------------------------------------------------------------------
    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> "LemmaLookup":
        return self

    def __exit__(self, *_) -> None:
        self.close()


class Lookup:
    """Thin adapter so run.py can load this pipeline via the standard interface."""

    def __init__(self, src: str, tgt: str) -> None:
        self._inner = LemmaLookup()

    def lookup(self, lemma: str, grammar: str = "") -> list[str]:
        return self._inner.lookup(lemma)

    def close(self) -> None:
        self._inner.close()
