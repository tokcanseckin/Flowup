"""
Build a local ru→tr SQLite DB from the ruwiktionary raw wiktextract dump.

Source dump (shared across all pipelines using ruwiktionary):
    eval/sources/ruwiktionary/raw-wiktextract-data.jsonl.gz   (~272 MB)

Output DB (this pipeline only):
    eval/pipelines/ru-tr/kaikki_1/data/ru_tr.db

Download the source dump once:
    curl -L -o eval/sources/ruwiktionary/raw-wiktextract-data.jsonl.gz \\
         https://kaikki.org/ruwiktionary/raw-wiktextract-data.jsonl.gz

Then build the DB:
    python -m eval.pipelines.ru_tr.kaikki_1.build_db

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
import unicodedata
import urllib.request
from pathlib import Path

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
_HERE = Path(__file__).parent
_EVAL_ROOT = _HERE.parents[3]          # eval/

SOURCE_URL = "https://kaikki.org/ruwiktionary/raw-wiktextract-data.jsonl.gz"
DEFAULT_SOURCE = _EVAL_ROOT / "sources" / "ruwiktionary" / "raw-wiktextract-data.jsonl.gz"
DEFAULT_DB = _HERE / "data" / "ru_tr.db"

# POS tags to include (ruwiktionary uses Russian labels internally, but
# wiktextract normalises them to English in the `pos` field)
VALID_POS = {"noun", "verb", "adj", "adv", "prep", "conj", "pron", "particle", "name"}

DDL = """
CREATE TABLE IF NOT EXISTS definitions (
    lemma     TEXT NOT NULL,
    pos       TEXT,
    tr_word   TEXT NOT NULL,
    tr_sense  TEXT
);
CREATE INDEX IF NOT EXISTS idx_lemma ON definitions (lemma);
"""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _strip_stress(text: str) -> str:
    return "".join(
        c for c in unicodedata.normalize("NFD", text)
        if unicodedata.category(c) != "Mn"
    )


def _extract_tr_rows(entry: dict, lemma: str, pos: str) -> list[tuple]:
    """Return (lemma, pos, tr_word, tr_sense) tuples for all Turkish translations."""
    rows: list[tuple] = []

    def _is_tr(t: dict) -> bool:
        return (
            t.get("lang_code") == "tr"
            or t.get("lang", "").lower() in ("turkish", "турецкий")
        ) and bool(t.get("word"))

    # Top-level translations
    for t in entry.get("translations", []):
        if _is_tr(t):
            rows.append((lemma, pos, t["word"], t.get("sense") or ""))

    # Sense-level translations
    for sense in entry.get("senses", []):
        gloss = (sense.get("glosses") or [""])[0]
        for t in sense.get("translations", []):
            if _is_tr(t):
                rows.append((lemma, pos, t["word"], t.get("sense") or gloss or ""))

    return rows


# ---------------------------------------------------------------------------
# Build
# ---------------------------------------------------------------------------

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

            # The raw dump contains ALL languages; keep only Russian entries.
            if entry.get("lang_code", "ru") != "ru":
                skipped_lang += 1
                continue

            lemma = _strip_stress(entry.get("word", "")).lower()
            if not lemma:
                continue

            pos = entry.get("pos", "")
            rows = _extract_tr_rows(entry, lemma, pos)

            if rows:
                conn.executemany(
                    "INSERT INTO definitions (lemma, pos, tr_word, tr_sense) VALUES (?,?,?,?)",
                    rows,
                )
                inserted += len(rows)

            if (i + 1) % 100_000 == 0:
                conn.commit()
                print(f"  {i + 1:,} lines … {inserted:,} TR rows so far "
                      f"(skipped {skipped_lang:,} non-ru)")

    conn.commit()
    conn.close()
    print(f"\nDone. {inserted:,} ru→tr rows written to {db_path}")


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
        print("(~272 MB compressed; resumes on failure)")

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
        description="Build ru→tr SQLite DB from the ruwiktionary raw dump."
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
        help="Download the dump to --source before building",
    )
    args = parser.parse_args()

    if args.download:
        download(SOURCE_URL, args.source)

    if not args.source.exists():
        print(
            f"ERROR: dump not found at {args.source}\n"
            "Run with --download, or manually:\n"
            f"  curl -L -o {args.source} {SOURCE_URL}",
            file=sys.stderr,
        )
        sys.exit(1)

    build_db(args.source, args.db)


if __name__ == "__main__":
    main()
