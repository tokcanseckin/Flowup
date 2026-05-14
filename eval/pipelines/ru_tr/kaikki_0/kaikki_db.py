"""
Download and parse the kaikki.org Russian dictionary into a local SQLite DB
at eval/data/ru_tr.db, extracting only ru→tr translations.

The kaikki.org dump is ~800 MB JSONL. This runs once; subsequent eval runs
use the cached DB. The downloaded JSON is deleted after import by default.

Usage:
    python -m eval.kaikki_db
    python -m eval.kaikki_db --file /path/to/kaikki.org-dictionary-Russian.json
    python -m eval.kaikki_db --keep      # keep downloaded JSON
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import unicodedata
import urllib.request
from pathlib import Path

KAIKKI_URL = (
    "https://kaikki.org/dictionary/Russian/kaikki.org-dictionary-Russian.jsonl"
)
DB_PATH = Path(__file__).parent / "data" / "ru_tr.db"

DDL = """
CREATE TABLE IF NOT EXISTS definitions (
    lemma     TEXT NOT NULL,
    pos       TEXT,
    tr_word   TEXT NOT NULL,
    tr_sense  TEXT
);
CREATE INDEX IF NOT EXISTS idx_lemma ON definitions (lemma);
"""


def strip_stress(text: str) -> str:
    """Remove combining accent marks (stress) from Cyrillic text."""
    return "".join(
        c for c in unicodedata.normalize("NFD", text)
        if unicodedata.category(c) != "Mn"
    )


def _extract_tr(entry: dict, lemma: str, pos: str) -> list[tuple]:
    """Extract all ru→tr translation rows from an entry.

    kaikki entries may carry translations at the top level OR nested inside
    each sense; we check both locations.
    """
    rows: list[tuple] = []

    def _is_tr(t: dict) -> bool:
        return (t.get("lang_code") == "tr" or t.get("lang", "").lower() == "turkish") and bool(t.get("word"))

    # Top-level translations list
    for t in entry.get("translations", []):
        if _is_tr(t):
            rows.append((lemma, pos, t["word"], t.get("sense", "") or ""))

    # Sense-level translations list
    for sense in entry.get("senses", []):
        gloss = (sense.get("glosses") or [""])[0]
        for t in sense.get("translations", []):
            if _is_tr(t):
                rows.append((lemma, pos, t["word"], t.get("sense", "") or gloss))

    return rows


def build_db(json_path: Path, db_path: Path) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.executescript(DDL)

    inserted = 0
    with open(json_path, encoding="utf-8") as f:
        for i, line in enumerate(f):
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue

            # The Russian dump contains only Russian entries, but skip any
            # stray entries from other languages just in case.
            lc = entry.get("lang_code", "ru")
            if lc and lc != "ru":
                continue

            lemma = strip_stress(entry.get("word", "")).lower()
            pos = entry.get("pos", "")

            tr_rows = _extract_tr(entry, lemma, pos)

            if tr_rows:
                conn.executemany(
                    "INSERT INTO definitions (lemma, pos, tr_word, tr_sense) VALUES (?,?,?,?)",
                    tr_rows,
                )
                inserted += len(tr_rows)

            if (i + 1) % 50_000 == 0:
                conn.commit()
                print(f"  {i + 1:,} lines processed, {inserted:,} TR translations stored …")

    conn.commit()
    conn.close()
    print(f"\nDone. {inserted:,} ru→tr entries written to {db_path}")


def _report(count: int, block: int, total: int) -> None:
    done = count * block
    if total > 0:
        pct = min(done / total * 100, 100)
        print(f"\r  {done / 1e6:.1f} MB / {total / 1e6:.0f} MB  ({pct:.0f}%)", end="", flush=True)
    else:
        print(f"\r  {done / 1e6:.1f} MB downloaded", end="", flush=True)


def download(url: str, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)

    existing = dest.stat().st_size if dest.exists() else 0
    headers = {}
    if existing:
        headers["Range"] = f"bytes={existing}-"
        print(f"Resuming download from {existing / 1e6:.1f} MB …")
    else:
        print(f"Downloading {url}")
        print("(~930 MB — may take several minutes; resumes automatically on failure)")

    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            total = existing + int(resp.headers.get("Content-Length", 0) or 0)
            mode = "ab" if existing else "wb"
            done = existing
            block = 1 << 15  # 32 KB
            with dest.open(mode) as f:
                while True:
                    chunk = resp.read(block)
                    if not chunk:
                        break
                    f.write(chunk)
                    done += len(chunk)
                    if total:
                        pct = min(done / total * 100, 100)
                        print(f"\r  {done / 1e6:.1f} MB / {total / 1e6:.0f} MB  ({pct:.0f}%)", end="", flush=True)
                    else:
                        print(f"\r  {done / 1e6:.1f} MB downloaded", end="", flush=True)
    except Exception as e:
        print(f"\nConnection interrupted ({e}). Re-run to resume.", file=sys.stderr)
        raise
    print()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Build a local ru→tr SQLite DB from the kaikki.org Russian dump."
    )
    parser.add_argument(
        "--file", type=Path, default=None,
        help="Path to an already-downloaded kaikki JSONL file (skips download)"
    )
    parser.add_argument(
        "--db", type=Path, default=DB_PATH,
        help=f"Output SQLite path (default: {DB_PATH})"
    )
    parser.add_argument(
        "--keep", action="store_true",
        help="Keep the downloaded JSON after import (default: delete it)"
    )
    args = parser.parse_args()

    if args.file:
        json_path = args.file
        if not json_path.exists():
            print(f"ERROR: {json_path} does not exist.", file=sys.stderr)
            sys.exit(1)
    else:
        json_path = Path(__file__).parent / "data" / "kaikki-ru.jsonl"
        if not json_path.exists():
            download(KAIKKI_URL, json_path)

    print(f"Parsing {json_path} …")
    build_db(json_path, args.db)

    if not args.keep and args.file is None and json_path.exists():
        json_path.unlink()
        print("Downloaded JSON removed (pass --keep to retain it).")


if __name__ == "__main__":
    main()
