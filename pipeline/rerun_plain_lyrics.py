#!/usr/bin/env python3
"""
rerun_plain_lyrics.py — Re-process songs that were saved with plain (unsynced)
lyrics and replace them if LRCLIB now has synced lyrics.

A song is considered "plain-lyrics" when ALL of its lines have start_time_ms == 0
(the fallback format: [00:00.00] prefix applied to every line during import).

Usage:
    python rerun_plain_lyrics.py --api-url https://singoling.com --lang ru \\
        --admin-token "1.xxx" --playlist-id 5 [--dry-run]
"""
from __future__ import annotations

import argparse
import subprocess
import sys
import time
from pathlib import Path

import requests

PIPELINE_DIR = Path(__file__).parent


# ── Helpers ───────────────────────────────────────────────────────────────────

def is_plain_lyrics_song(song: dict) -> bool:
    """Return True if every lyric line has start_time_ms == 0 (plain fallback)."""
    lines = song.get("lines", [])
    if not lines:
        return False
    return all(line.get("start_time_ms", -1) == 0 for line in lines)


def fetch_song_detail(api_url: str, song_id: int) -> dict | None:
    try:
        r = requests.get(f"{api_url.rstrip('/')}/api/songs/{song_id}", timeout=15)
        r.raise_for_status()
        return r.json()
    except requests.RequestException as exc:
        print(f"  [API] Could not fetch song {song_id}: {exc}")
        return None


def fetch_songs(api_url: str, lang: str | None) -> list[dict]:
    r = requests.get(f"{api_url.rstrip('/')}/api/songs", timeout=15)
    r.raise_for_status()
    songs = r.json()
    if lang:
        songs = [s for s in songs if s.get("language_code") == lang]
    return songs


def has_synced_on_lrclib(artist: str, title: str, lang: str) -> bool:
    """Quick check: does LRCLIB have synced lyrics for this song right now?"""
    # Import the same function the pipeline uses (avoids code duplication)
    sys.path.insert(0, str(PIPELINE_DIR))
    from generate_song_data import fetch_synced_lyrics  # noqa: PLC0415
    result = fetch_synced_lyrics(artist, title, lang)
    return result is not None


def reprocess(
    song_id: int,
    artist: str,
    title: str,
    lang: str,
    playlist_id: int | None,
    admin_token: str,
    api_url: str,
    dry_run: bool,
) -> bool:
    print(f"  → Re-running generate_song_data.py for song id={song_id} …")
    if dry_run:
        print("  [dry-run] Would run: generate_song_data.py "
              f"--replace-id {song_id} --artist '{artist}' --title '{title}'")
        return True

    cmd = [
        sys.executable, str(PIPELINE_DIR / "generate_song_data.py"),
        "--lang", lang,
        "--artist", artist,
        "--title", title,
        "--api-url", api_url,
        "--admin-token", admin_token,
        "--replace-id", str(song_id),
    ]
    if playlist_id:
        cmd += ["--playlist-id", str(playlist_id)]

    result = subprocess.run(cmd, cwd=str(PIPELINE_DIR))
    return result.returncode == 0


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--api-url",     required=True,  dest="api_url")
    p.add_argument("--lang",        default=None,   help="Filter by language code (e.g. ru)")
    p.add_argument("--admin-token", required=True,  dest="admin_token")
    p.add_argument("--playlist-id", default=None,   dest="playlist_id", type=int,
                   help="Re-add replaced songs to this playlist")
    p.add_argument("--dry-run",     action="store_true", dest="dry_run",
                   help="Check and report without making any changes")
    args = p.parse_args()

    print(f"Fetching songs from {args.api_url} …")
    songs = fetch_songs(args.api_url, args.lang)
    print(f"Total songs (lang={args.lang or 'all'}): {len(songs)}")

    # Identify plain-lyrics songs by fetching their full line data
    plain_songs = []
    print("Scanning for plain-lyrics songs …")
    for s in songs:
        detail = fetch_song_detail(args.api_url, s["id"])
        if detail and is_plain_lyrics_song(detail):
            plain_songs.append(detail)
        time.sleep(0.05)  # be gentle

    if not plain_songs:
        print("No plain-lyrics songs found. Nothing to do.")
        return

    print(f"\nFound {len(plain_songs)} plain-lyrics song(s):")
    for s in plain_songs:
        print(f"  id={s['id']:4d}  {s.get('artist', '?')} — {s.get('title', '?')}")

    print()
    updated = 0
    skipped = 0

    for i, song in enumerate(plain_songs, 1):
        song_id = song["id"]
        artist  = song.get("artist", "")
        title   = song.get("title", "")
        lang    = song.get("language", {}).get("code") or args.lang or ""

        print(f"[{i}/{len(plain_songs)}] {artist} — {title}  (id={song_id})")

        # Check LRCLIB for synced lyrics right now
        if not has_synced_on_lrclib(artist, title, lang):
            print("  ✗ Still no synced lyrics on LRCLIB — skipping.")
            skipped += 1
            time.sleep(0.5)
            continue

        print("  ✓ Synced lyrics found on LRCLIB!")
        ok = reprocess(
            song_id=song_id,
            artist=artist,
            title=title,
            lang=lang,
            playlist_id=args.playlist_id,
            admin_token=args.admin_token,
            api_url=args.api_url,
            dry_run=args.dry_run,
        )
        if ok:
            print(f"  ✓ {'Would replace' if args.dry_run else 'Replaced'} song id={song_id}.")
            updated += 1
        else:
            print(f"  ✗ generate_song_data.py failed for id={song_id}.")
            skipped += 1

        time.sleep(1.0)

    print("\n── Summary " + "─" * 40)
    print(f"  Replaced / updated : {updated}")
    print(f"  No synced found    : {skipped}")


if __name__ == "__main__":
    main()
