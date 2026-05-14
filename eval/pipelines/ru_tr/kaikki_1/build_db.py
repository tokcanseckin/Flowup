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
_EVAL_ROOT = _HERE.parents[2]          # eval/

SOURCE_URL = "https://kaikki.org/ruwiktionary/raw-wiktextract-data.jsonl.gz"
DEFAULT_SOURCE = _EVAL_ROOT / "sources" / "ruwiktionary" / "raw-wiktextract-data.jsonl.gz"
DEFAULT_DB = _HERE / "data" / "ru_tr.db"

# Hop-DB paths and sources (for two-hop ru→en→tr / ru→de→tr fallback)
RU_EN_DB = _HERE / "data" / "ru_en.db"
RU_DE_DB = _HERE / "data" / "ru_de.db"
EN_TR_DB = _HERE / "data" / "en_tr.db"
DE_TR_DB = _HERE / "data" / "de_tr.db"

EN_SOURCE = _EVAL_ROOT / "sources" / "enwiktionary" / "raw-wiktextract-data.jsonl.gz"
DE_SOURCE = _EVAL_ROOT / "sources" / "dewiktionary" / "raw-wiktextract-data.jsonl.gz"

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

HOP_DDL = """
CREATE TABLE IF NOT EXISTS translations (
    lemma    TEXT NOT NULL,
    tgt_word TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_lemma ON translations (lemma);
"""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _strip_stress(text: str) -> str:
    # Only remove the combining acute accent (U+0301) used for Russian stress marks.
    # Preserve й (и + breve) and other combining letters — they recompose via NFC.
    # Normalise ё → е explicitly since pymorphy3 normal_form uses е.
    nfd = unicodedata.normalize("NFD", text)
    stripped = "".join(c for c in nfd if c != "\u0301")
    return unicodedata.normalize("NFC", stripped).replace("ё", "е").replace("Ё", "Е")


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


def _extract_hop_rows(entry: dict, lemma_norm: str, trans_lang_code: str) -> list[tuple]:
    """Return (lemma, tgt_word) tuples for translations to *trans_lang_code*."""
    rows: list[tuple] = []

    def _is_target(t: dict) -> bool:
        return t.get("lang_code") == trans_lang_code and bool(t.get("word"))

    for t in entry.get("translations", []):
        if _is_target(t):
            rows.append((lemma_norm, t["word"]))

    for sense in entry.get("senses", []):
        for t in sense.get("translations", []):
            if _is_target(t):
                rows.append((lemma_norm, t["word"]))

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


def build_hop_db(
    source: Path,
    db_path: Path,
    entry_lang_code: str,
    trans_lang_code: str,
    lemma_normalizer=None,
) -> None:
    """Build a single-hop lemma→tgt_word DB (e.g. ru→en or en→tr)."""
    if lemma_normalizer is None:
        lemma_normalizer = str.lower

    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.executescript(HOP_DDL)

    opener = gzip.open if source.suffix == ".gz" else open
    inserted = 0
    skipped = 0

    print(f"Building {entry_lang_code}→{trans_lang_code} DB from {source} …")
    with opener(source, "rt", encoding="utf-8") as f:
        for i, line in enumerate(f):
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue

            if entry.get("lang_code", "") != entry_lang_code:
                skipped += 1
                continue

            raw_word = entry.get("word", "") or ""
            lemma = lemma_normalizer(raw_word)
            if not lemma:
                continue

            rows = _extract_hop_rows(entry, lemma, trans_lang_code)
            if rows:
                conn.executemany(
                    "INSERT INTO translations (lemma, tgt_word) VALUES (?,?)",
                    rows,
                )
                inserted += len(rows)

            if (i + 1) % 200_000 == 0:
                conn.commit()
                print(f"  {i + 1:,} lines … {inserted:,} rows (skipped {skipped:,} non-{entry_lang_code})")

    conn.commit()
    conn.close()
    print(f"Done. {inserted:,} {entry_lang_code}→{trans_lang_code} rows → {db_path}")


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
    parser.add_argument(
        "--build-hops", action="store_true",
        help=(
            "Also build the four two-hop intermediate DBs "
            "(ru_en, ru_de from ruwiktionary; en_tr from enwiktionary; de_tr from dewiktionary). "
            "Requires eval/sources/enwiktionary/ and eval/sources/dewiktionary/ dumps to exist."
        ),
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

    if args.build_hops:
        ru_norm = lambda w: _strip_stress(w).lower()  # noqa: E731

        print()
        build_hop_db(args.source, RU_EN_DB, "ru", "en", lemma_normalizer=ru_norm)
        print()
        build_hop_db(args.source, RU_DE_DB, "ru", "de", lemma_normalizer=ru_norm)

        for label, src, db, src_lang in [
            ("en→tr", EN_SOURCE, EN_TR_DB, "en"),
            ("de→tr", DE_SOURCE, DE_TR_DB, "de"),
        ]:
            print()
            if not src.exists():
                print(f"SKIP {label}: {src} not found")
                continue
            build_hop_db(src, db, src_lang, "tr")


if __name__ == "__main__":
    main()
