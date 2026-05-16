"""
Build a local en→es SQLite DB from the enwiktionary raw wiktextract dump.

Source dump (shared with en_ru):
    eval/sources/enwiktionary/raw-wiktextract-data.jsonl.gz   (~2.5 GB)

Output DB:
    eval/pipelines/en_es/kaikki_1/data/en_es.db

Build:
    python -m eval.pipelines.en_es.kaikki_1.build_db

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
DEFAULT_DB = _HERE / "data" / "en_es.db"

DDL = """
DROP TABLE IF EXISTS definitions;
CREATE TABLE definitions (
    lemma     TEXT NOT NULL,
    pos       TEXT,
    es_word   TEXT NOT NULL,
    es_sense  TEXT
);
CREATE INDEX idx_lemma ON definitions (lemma);
"""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_SKIP_TAGS: frozenset[str] = frozenset({
    "slang", "vulgar", "offensive", "dialectal",
    "pejorative", "derogatory", "taboo",
})


def _extract_es_rows(entry: dict, lemma: str, pos: str) -> list[tuple]:
    """Return (lemma, pos, es_word, es_sense) tuples for all Spanish translations.

    Translations tagged as slang, dialectal, vulgar, etc. are excluded so the
    DB contains only standard (peninsular + neutral Latin American) entries.
    """
    rows: list[tuple] = []

    def _is_es(t: dict) -> bool:
        if not (
            t.get("lang_code") == "es"
            or t.get("lang", "").lower() in ("spanish", "español")
        ) or not t.get("word"):
            return False
        tags = set(t.get("tags") or [])
        return not (tags & _SKIP_TAGS)

    def _clean_es_word(word: str) -> str:
        return word.strip()

    # Top-level translations
    for t in entry.get("translations", []):
        if _is_es(t):
            es_word = _clean_es_word(t["word"])
            sense_text = t.get("sense") or ""
            if es_word:
                rows.append((lemma, pos, es_word, sense_text))

    # Sense-level translations
    for sense in entry.get("senses", []):
        gloss = (sense.get("glosses") or [""])[0]
        for t in sense.get("translations", []):
            if _is_es(t):
                es_word = _clean_es_word(t["word"])
                sense_text = t.get("sense") or gloss or ""
                if es_word:
                    rows.append((lemma, pos, es_word, sense_text))

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

            # Keep only English word entries
            if entry.get("lang_code", "") != "en":
                skipped_lang += 1
                continue

            lemma = entry.get("word", "").lower().strip()
            if not lemma:
                continue

            pos = entry.get("pos", "")
            rows = _extract_es_rows(entry, lemma, pos)

            if rows:
                conn.executemany(
                    "INSERT INTO definitions (lemma, pos, es_word, es_sense) VALUES (?,?,?,?)",
                    rows,
                )
                inserted += len(rows)

            if (i + 1) % 100_000 == 0:
                conn.commit()
                print(f"  {i + 1:,} lines … {inserted:,} ES rows so far "
                      f"(skipped {skipped_lang:,} non-en)")

    conn.commit()
    conn.close()
    print(f"\nDone. {inserted:,} en→es rows written to {db_path}")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Build en→es SQLite DB from the enwiktionary raw dump."
    )
    parser.add_argument(
        "--source", type=Path, default=DEFAULT_SOURCE,
        help=f"Path to dump file (default: {DEFAULT_SOURCE})",
    )
    parser.add_argument(
        "--db", type=Path, default=DEFAULT_DB,
        help=f"Output DB path (default: {DEFAULT_DB})",
    )
    args = parser.parse_args()

    if not args.source.exists():
        print(f"ERROR: source not found: {args.source}", file=sys.stderr)
        print("Download it first:", file=sys.stderr)
        print("  curl -L -o eval/sources/enwiktionary/raw-wiktextract-data.jsonl.gz \\", file=sys.stderr)
        print("       https://kaikki.org/enwiktionary/raw-wiktextract-data.jsonl.gz", file=sys.stderr)
        sys.exit(1)

    build_db(args.source, args.db)


if __name__ == "__main__":
    main()
