#!/usr/bin/env python3
"""
Batch import songs from a CSV into a FlowUp playlist.

Usage:
    python import_playlist.py \
        --csv ../content_to_add/songs/beginner_russian_playlist.csv \
        --lang ru \
        --playlist-id 4 \
        --admin-token "1.xxxx" \
        --api-url https://singoling.com \
        [--start-at 1]   # resume from row N (1-based) if interrupted
"""

import argparse
import csv
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

PIPELINE_DIR = Path(__file__).parent

# Artist values in the CSV that are too generic for LRCLIB lookup.
# When an artist matches, we search by title only (empty artist string).
_GENERIC_KEYWORDS = {
    "traditional", "folk", "soviet", "cartoon", "film", "song",
    "romance", "cheburashka", "elektronik", "prostokvashino",
    "caucasian", "future", "kozlov", "blanter",
}


def _effective_artist(raw_artist: str) -> str:
    """Return empty string for generic/non-searchable artist values."""
    low = raw_artist.lower()
    if any(kw in low for kw in _GENERIC_KEYWORDS):
        return ""
    return raw_artist


def _ts() -> str:
    return datetime.now().strftime("%H:%M:%S")


def run_song(artist: str, title: str, lang: str, api_url: str,
             playlist_id: int, admin_token: str) -> bool:
    cmd = [
        sys.executable, str(PIPELINE_DIR / "generate_song_data.py"),
        "--lang", lang,
        "--artist", artist,
        "--title", title,
        "--api-url", api_url,
        "--playlist-id", str(playlist_id),
        "--admin-token", admin_token,
    ]
    result = subprocess.run(cmd, cwd=str(PIPELINE_DIR))
    return result.returncode == 0


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--csv",          required=True)
    p.add_argument("--lang",         required=True)
    p.add_argument("--playlist-id",  dest="playlist_id", type=int, required=True)
    p.add_argument("--admin-token",  dest="admin_token", required=True)
    p.add_argument("--api-url",      dest="api_url", required=True)
    p.add_argument("--start-at",     dest="start_at", type=int, default=1,
                   help="1-based row number to start/resume from")
    p.add_argument("--skip",          dest="skip_rows", default="",
                   help="Comma-separated 1-based row numbers to skip")
    args = p.parse_args()

    skip_set = {int(x) for x in args.skip_rows.split(",") if x.strip()}

    rows = []
    with open(args.csv, encoding="utf-8") as f:
        for row in csv.DictReader(f):
            rows.append(row)

    total = len(rows)
    failed = []
    succeeded = 0
    started_at = datetime.now()

    print(f"[{_ts()}] Starting import — {total} songs total")
    print("=" * 60)

    for i, row in enumerate(rows, start=1):
        if i < args.start_at:
            continue

        if i in skip_set:
            print(f"  [skip] Row {i}: in --skip list")
            continue

        title      = row.get("song_cyrillic", "").strip()
        raw_artist = row.get("artist", "").strip()
        artist     = _effective_artist(raw_artist)

        if not title:
            print(f"  [skip] Row {i}: missing title")
            continue

        artist_display = artist or f"(title-only, was: {raw_artist})"
        print(f"\n[{_ts()}] [{i}/{total}] {title} — {artist_display}")
        print("-" * 60)

        t0 = time.perf_counter()
        ok = run_song(
            artist=artist,
            title=title,
            lang=args.lang,
            api_url=args.api_url,
            playlist_id=args.playlist_id,
            admin_token=args.admin_token,
        )
        elapsed = time.perf_counter() - t0

        if ok:
            succeeded += 1
            print(f"[{_ts()}] ✓ [{i}/{total}]  OK  ({elapsed:.0f}s)  "
                  f"progress: {succeeded} done, {len(failed)} failed")
        else:
            failed.append((i, raw_artist, title))
            print(f"[{_ts()}] ✗ [{i}/{total}]  FAIL ({elapsed:.0f}s)  "
                  f"progress: {succeeded} done, {len(failed)} failed")

        # Brief pause between songs to be polite to LRCLIB/DeepL
        if i < total:
            time.sleep(3)

    duration = datetime.now() - started_at
    print(f"\n{'='*60}")
    print(f"[{_ts()}] Import complete in {str(duration).split('.')[0]}")
    print(f"  {succeeded}/{total} succeeded  |  {len(failed)} failed")
    if failed:
        print(f"\n  Failed songs:")
        for num, art, tit in failed:
            print(f"    Row {num}: {art!r} – {tit}")
        print(f"\n  To retry failures, rerun with --start-at <row_num>")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()
