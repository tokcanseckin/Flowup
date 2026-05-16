"""
Build a local en→de SQLite DB from the enwiktionary raw wiktextract dump.

Source dump (shared with en_ru, en_es):
    eval/sources/enwiktionary/raw-wiktextract-data.jsonl.gz   (~2.5 GB)

Output DB:
    eval/pipelines/en_de/kaikki_1/data/en_de.db

Build:
    python -m eval.pipelines.en_de.kaikki_1.build_db

Options:
    --source PATH   Override path to the .jsonl or .jsonl.gz dump
    --db PATH       Override output DB path
"""

from __future__ import annotations

import argparse
import gzip
import json
import sqlite3
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
_HERE = Path(__file__).parent
_EVAL_ROOT = _HERE.parents[2]          # eval/

DEFAULT_SOURCE = _EVAL_ROOT / "sources" / "enwiktionary" / "raw-wiktextract-data.jsonl.gz"
DEFAULT_DB = _HERE / "data" / "en_de.db"

DDL = """
DROP TABLE IF EXISTS definitions;
CREATE TABLE definitions (
    lemma     TEXT NOT NULL,
    pos       TEXT,
    de_word   TEXT NOT NULL,
    de_sense  TEXT
);
CREATE INDEX idx_lemma ON definitions (lemma);
"""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_SKIP_TAGS: frozenset[str] = frozenset({
    "slang", "vulgar", "offensive", "dialectal",
    "pejorative", "derogatory", "taboo",
    "archaic", "dated", "obsolete",
})


def _extract_de_rows(entry: dict, lemma: str, pos: str) -> list[tuple]:
    """Return (lemma, pos, de_word, de_sense) tuples for all German translations.

    Translations tagged as slang, dialectal, archaic, etc. are excluded so the
    DB contains standard High German entries only.
    """
    rows: list[tuple] = []

    def _is_de(t: dict) -> bool:
        if not (
            t.get("lang_code") == "de"
            or t.get("lang", "").lower() in ("german", "deutsch")
        ) or not t.get("word"):
            return False
        tags = set(t.get("tags") or [])
        return not (tags & _SKIP_TAGS)

    def _clean_de_word(word: str) -> str:
        # Strip parenthetical gender markers like "(m)", "(f)", "(n)", "(pl)"
        import re
        word = re.sub(r"\s*\([^)]*\)", "", word)
        return word.strip()

    # Top-level translations
    for t in entry.get("translations", []):
        if _is_de(t):
            de_word = _clean_de_word(t["word"])
            sense_text = t.get("sense") or ""
            if de_word:
                rows.append((lemma, pos, de_word, sense_text))

    # Sense-level translations
    for sense in entry.get("senses", []):
        gloss = (sense.get("glosses") or [""])[0]
        for t in sense.get("translations", []):
            if _is_de(t):
                de_word = _clean_de_word(t["word"])
                sense_text = t.get("sense") or gloss or ""
                if de_word:
                    rows.append((lemma, pos, de_word, sense_text))

    return rows


def build_db(source: Path, db_path: Path) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(db_path)
    conn.executescript(DDL)

    opener = gzip.open if source.suffix == ".gz" else open
    inserted = 0
    skipped_lang = 0

    print(f"Reading {source} …")
    with opener(source, "rt", encoding="utf-8") as f:
        for i, line in enumerate(f):
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue

            # Keep only English word entries (enwiktionary source)
            if entry.get("lang_code") != "en" and entry.get("lang", "").lower() != "english":
                skipped_lang += 1
                continue

            lemma = entry.get("word", "").strip().lower()
            pos   = entry.get("pos", "").strip().lower()
            if not lemma or not pos:
                continue

            rows = _extract_de_rows(entry, lemma, pos)
            if rows:
                conn.executemany(
                    "INSERT INTO definitions (lemma, pos, de_word, de_sense) VALUES (?, ?, ?, ?)",
                    rows,
                )
                inserted += len(rows)

            if (i + 1) % 100_000 == 0:
                conn.commit()
                print(f"  {i + 1:,} entries processed, {inserted:,} rows inserted …", flush=True)

    conn.commit()
    conn.close()
    print(f"\nDone. {inserted:,} rows written to {db_path}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Build en→de SQLite DB from enwiktionary dump.")
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--db",     type=Path, default=DEFAULT_DB)
    args = parser.parse_args()

    if not args.source.exists():
        sys.exit(f"Source dump not found: {args.source}")

    build_db(args.source, args.db)


if __name__ == "__main__":
    main()
