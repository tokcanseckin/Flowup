#!/usr/bin/env python3
"""
fill_apple_music_urls.py — Populate missing Apple Music URLs for songs in the
SingoLing backend using the public iTunes Search API (no auth required).

Usage:
    python fill_apple_music_urls.py --api-url http://127.0.0.1:8000

    # Preview matches without writing anything:
        --dry-run

    # Limit to a specific storefront (default: us):
        --storefront ru

    # Only process songs in a specific language:
        --lang ru

    # Overwrite songs that already have an Apple Music URL:
        --overwrite

    # Target the live server:
        --api-url https://singoling.com

Examples:
    python fill_apple_music_urls.py --api-url http://127.0.0.1:8000 --dry-run
    python fill_apple_music_urls.py --api-url https://singoling.com --lang ru
"""

from __future__ import annotations

import argparse
import time
import unicodedata
from difflib import SequenceMatcher
from typing import Optional
from urllib.parse import urlencode

import requests

# ── iTunes Search API ──────────────────────────────────────────────────────────

_ITUNES_SEARCH = "https://itunes.apple.com/search"

# Minimum fuzzy similarity (0-1) required for title and artist matches.
_MIN_TITLE_SIMILARITY = 0.6
_MIN_ARTIST_SIMILARITY = 0.5

# Seconds between iTunes requests — stay well under Apple's undocumented limit.
_ITUNES_RATE_LIMIT_S = 0.5


def _normalise(s: str) -> str:
    """Lowercase, strip diacritics, collapse whitespace for fuzzy comparison."""
    s = s.lower().strip()
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    return " ".join(s.split())


def _similarity(a: str, b: str) -> float:
    return SequenceMatcher(None, _normalise(a), _normalise(b)).ratio()


def search_apple_music(
    title: str,
    artist: str,
    storefront: str = "us",
) -> Optional[str]:
    """
    Search iTunes for the best matching track and return its Apple Music URL,
    or None if no confident match is found.

    The returned URL is in the form:
      https://music.apple.com/<storefront>/album/<name>/<album-id>?i=<track-id>
    which MusicKit JS v3 can play directly.
    """
    query = f"{artist} {title}"
    params = {
        "term": query,
        "media": "music",
        "entity": "song",
        "limit": 10,
        "country": storefront,
    }
    url = f"{_ITUNES_SEARCH}?{urlencode(params)}"

    try:
        r = requests.get(url, timeout=10)
        r.raise_for_status()
        results = r.json().get("results", [])
    except requests.RequestException as exc:
        print(f"    [iTunes] Request failed: {exc}")
        return None

    best_url: Optional[str] = None
    best_score = 0.0

    for result in results:
        r_title = result.get("trackName", "")
        r_artist = result.get("artistName", "")
        r_url = result.get("trackViewUrl", "")

        if not r_url:
            continue

        title_sim = _similarity(title, r_title)
        artist_sim = _similarity(artist, r_artist)

        # Both must clear the minimum threshold.
        if title_sim < _MIN_TITLE_SIMILARITY or artist_sim < _MIN_ARTIST_SIMILARITY:
            continue

        score = (title_sim * 0.6) + (artist_sim * 0.4)
        if score > best_score:
            best_score = score
            best_url = r_url
            print(
                f"    [iTunes] Candidate  title={r_title!r}  artist={r_artist!r}"
                f"  title_sim={title_sim:.2f}  artist_sim={artist_sim:.2f}"
                f"  score={score:.2f}"
            )

    return best_url


# ── Backend helpers ────────────────────────────────────────────────────────────

def fetch_songs(api_url: str) -> list[dict]:
    r = requests.get(api_url.rstrip("/") + "/api/songs", timeout=15)
    r.raise_for_status()
    return r.json()


def patch_apple_music_url(api_url: str, song_id: int, apple_music_url: str) -> bool:
    url = api_url.rstrip("/") + f"/api/songs/{song_id}/sources"
    try:
        r = requests.patch(url, json={"apple_music_url": apple_music_url}, timeout=10)
        r.raise_for_status()
        return True
    except requests.RequestException as exc:
        print(f"    [Backend] PATCH failed for song {song_id}: {exc}")
        return False


# ── Main ───────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Fill missing Apple Music URLs for SingoLing songs via iTunes Search.",
    )
    parser.add_argument(
        "--api-url",
        default="http://127.0.0.1:8000",
        help="Base URL of the SingoLing backend (default: http://127.0.0.1:8000)",
    )
    parser.add_argument(
        "--storefront",
        default="us",
        help="iTunes storefront country code to search in (default: us)",
    )
    parser.add_argument(
        "--lang",
        default=None,
        help="Only process songs with this language code (e.g. ru, it)",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Also update songs that already have an Apple Music URL",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Search iTunes and print results but do not write to the backend",
    )
    args = parser.parse_args()

    print(f"Fetching songs from {args.api_url} …")
    all_songs = fetch_songs(args.api_url)
    print(f"Total songs: {len(all_songs)}")

    candidates = [
        s for s in all_songs
        if (args.overwrite or not s.get("apple_music_url"))
        and (args.lang is None or s.get("language_code") == args.lang)
    ]

    if not candidates:
        print("No songs need updating. Done.")
        return

    print(
        f"Songs to process: {len(candidates)}"
        + (" (overwrite mode)" if args.overwrite else "")
        + (" [DRY RUN]" if args.dry_run else "")
    )
    print()

    found = 0
    skipped = 0
    failed = 0

    for i, song in enumerate(candidates, 1):
        title = song.get("title") or ""
        artist = song.get("artist") or ""
        song_id = song["id"]
        existing_url = song.get("apple_music_url")

        print(
            f"[{i}/{len(candidates)}] {artist} — {title}"
            + (f"  (current: {existing_url})" if existing_url else "")
        )

        apple_url = search_apple_music(title, artist, storefront=args.storefront)

        if apple_url:
            print(f"    → Match: {apple_url}")
            if not args.dry_run:
                ok = patch_apple_music_url(args.api_url, song_id, apple_url)
                if ok:
                    found += 1
                    print("    ✓ Updated")
                else:
                    failed += 1
            else:
                found += 1
        else:
            print("    ✗ No confident match found")
            skipped += 1

        # Respect iTunes rate limit.
        if i < len(candidates):
            time.sleep(_ITUNES_RATE_LIMIT_S)

    print()
    print("── Summary " + "─" * 40)
    print(f"  Found / updated : {found}")
    print(f"  No match        : {skipped}")
    print(f"  Errors          : {failed}")
    if args.dry_run:
        print("  (dry-run — nothing was written)")


if __name__ == "__main__":
    main()
