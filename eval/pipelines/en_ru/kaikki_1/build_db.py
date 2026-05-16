"""
Build a local en→ru SQLite DB from the enwiktionary raw wiktextract dump.

Source dump:
    eval/sources/enwiktionary/raw-wiktextract-data.jsonl.gz   (~2.5 GB)

Output DB:
    eval/pipelines/en_ru/kaikki_1/data/en_ru.db

Download the source dump once:
    curl -L -o eval/sources/enwiktionary/raw-wiktextract-data.jsonl.gz \\
         https://kaikki.org/enwiktionary/raw-wiktextract-data.jsonl.gz

Then build the DB:
    python -m eval.pipelines.en_ru.kaikki_1.build_db

Options:
    --source PATH   Override path to the .jsonl or .jsonl.gz dump
    --db PATH       Override output DB path
    --download      Download the dump automatically before building
"""

from __future__ import annotations

import argparse
import gzip
import json
import sqlite3
import sys
import urllib.request
from pathlib import Path

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
_HERE = Path(__file__).parent
_EVAL_ROOT = _HERE.parents[2]          # eval/

SOURCE_URL = "https://kaikki.org/enwiktionary/raw-wiktextract-data.jsonl.gz"
DEFAULT_SOURCE = _EVAL_ROOT / "sources" / "enwiktionary" / "raw-wiktextract-data.jsonl.gz"
DEFAULT_DB = _HERE / "data" / "en_ru.db"

DDL = """
DROP TABLE IF EXISTS definitions;
CREATE TABLE definitions (
    lemma     TEXT NOT NULL,
    pos       TEXT,
    ru_word   TEXT NOT NULL,
    ru_sense  TEXT
);
CREATE INDEX idx_lemma ON definitions (lemma);
"""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _extract_ru_rows(entry: dict, lemma: str, pos: str) -> list[tuple]:
    """Return (lemma, pos, ru_word, ru_sense) tuples for all Russian translations."""
    rows: list[tuple] = []

    def _is_ru(t: dict) -> bool:
        return (
            t.get("lang_code") == "ru"
            or t.get("lang", "").lower() in ("russian", "русский")
        ) and bool(t.get("word"))

    def _clean_ru_word(word: str) -> str:
        """Strip stress marks from Russian output word for clean storage."""
        # Keep the word as-is — stress marks are useful for learners.
        # Just strip leading/trailing whitespace.
        return word.strip()

    # Top-level translations
    for t in entry.get("translations", []):
        if _is_ru(t):
            ru_word = _clean_ru_word(t["word"])
            sense_text = t.get("sense") or ""
            if ru_word:
                rows.append((lemma, pos, ru_word, sense_text))

    # Sense-level translations
    for sense in entry.get("senses", []):
        gloss = (sense.get("glosses") or [""])[0]
        for t in sense.get("translations", []):
            if _is_ru(t):
                ru_word = _clean_ru_word(t["word"])
                sense_text = t.get("sense") or gloss or ""
                if ru_word:
                    rows.append((lemma, pos, ru_word, sense_text))

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
            rows = _extract_ru_rows(entry, lemma, pos)

            if rows:
                conn.executemany(
                    "INSERT INTO definitions (lemma, pos, ru_word, ru_sense) VALUES (?,?,?,?)",
                    rows,
                )
                inserted += len(rows)

            if (i + 1) % 100_000 == 0:
                conn.commit()
                print(f"  {i + 1:,} lines … {inserted:,} RU rows so far "
                      f"(skipped {skipped_lang:,} non-en)")

    conn.commit()
    conn.close()
    print(f"\nDone. {inserted:,} en→ru rows written to {db_path}")


# ---------------------------------------------------------------------------
# Download
# ---------------------------------------------------------------------------

def download(url: str, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    existing = dest.stat().st_size if dest.exists() else 0
    headers = {}
    if existing:
        headers["Range"] = f"bytes={existing}-"
        print(f"Resuming download from {existing / 1e6:.1f} MB …")
    else:
        print(f"Downloading {url}")
        print("(~2.5 GB compressed; resumes on failure)")

    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            total = existing + int(resp.headers.get("Content-Length", 0) or 0)
            mode = "ab" if existing else "wb"
            done = existing
            with dest.open(mode) as out:
                while True:
                    chunk = resp.read(1 << 15)
                    if not chunk:
                        break
                    out.write(chunk)
                    done += len(chunk)
                    if total:
                        pct = min(done / total * 100, 100)
                        print(f"\r  {done / 1e6:.1f} MB / {total / 1e6:.0f} MB ({pct:.0f}%)",
                              end="", flush=True)
                    else:
                        print(f"\r  {done / 1e6:.1f} MB", end="", flush=True)
    except Exception as exc:
        print(f"\nConnection interrupted ({exc}). Re-run to resume.", file=sys.stderr)
        raise
    print()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Build en→ru SQLite DB from the enwiktionary raw dump."
    )
    parser.add_argument(
        "--source", type=Path, default=DEFAULT_SOURCE,
        help=f"Path to dump file (default: {DEFAULT_SOURCE})",
    )
    parser.add_argument(
        "--db", type=Path, default=DEFAULT_DB,
        help=f"Output DB path (default: {DEFAULT_DB})",
    )
    parser.add_argument(
        "--download", action="store_true",
        help="Download the dump before building (skipped if already present).",
    )
    args = parser.parse_args()

    if args.download and not args.source.exists():
        download(SOURCE_URL, args.source)
    elif not args.source.exists():
        print(f"ERROR: source not found: {args.source}", file=sys.stderr)
        print("Run with --download to fetch it automatically.", file=sys.stderr)
        sys.exit(1)

    build_db(args.source, args.db)


if __name__ == "__main__":
    main()
