#!/usr/bin/env python3
"""
fill_youtube_urls.py — Populate missing YouTube URLs for songs in the
SingoLing backend using yt-dlp search (no API key required).

Usage:
    python fill_youtube_urls.py --api-url http://127.0.0.1:8000

    # Preview matches without writing anything:
        --dry-run

    # Only process songs in a specific language:
        --lang ru

    # Also show top candidates for rejected songs:
        --review

    # Overwrite songs that already have a YouTube URL:
        --overwrite

    # Target the live server:
        --api-url https://singoling.com

Examples:
    python fill_youtube_urls.py --api-url http://127.0.0.1:8000 --dry-run
    python fill_youtube_urls.py --api-url https://singoling.com --lang ru --review
"""

from __future__ import annotations

import argparse
import time
import unicodedata
from difflib import SequenceMatcher
from typing import Optional

try:
    from transliterate import translit as _cyr_translit
    _HAS_TRANSLITERATE = True
except ImportError:
    _HAS_TRANSLITERATE = False

import requests
import yt_dlp

# ── Scoring constants ──────────────────────────────────────────────────────────

# A title must reach this similarity to be considered.
_MIN_TITLE_SIMILARITY = 0.55

# Seconds between yt-dlp searches — be polite.
_RATE_LIMIT_S = 1.0

# How many search results to pull per query.
_SEARCH_LIMIT = 8

# Title words that strongly indicate a non-studio version.
_BLACKLIST = {
    "live", "cover", "remix", "acoustic", "karaoke", "instrumental",
    "tribute", "session", "concert", "in concert", "unplugged",
    "extended", "radio edit", "sped up", "slowed", "nightcore",
    "testo", "lyrics", "lyric video", "visualizer", "official video",
}

# Channels that reliably host studio recordings.  A result from one of these
# is treated as high-confidence even without a strong title match.
_TRUSTED_CHANNEL_KEYWORDS = {
    "topic",          # YouTube auto-generated "Artist - Topic" channels
    "vevo",           # VEVO
    "official",       # most label/artist official channels
    "records",        # label channels
    "music",          # label / distributor channels
    "dischi",         # Italian labels (e.g. Bomba Dischi)
    "label",
}

# ── Text helpers ──────────────────────────────────────────────────────────────

def _normalise(s: str) -> str:
    s = s.lower().strip()
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    return " ".join(s.split())


def _similarity(a: str, b: str) -> float:
    return SequenceMatcher(None, _normalise(a), _normalise(b)).ratio()


def _has_blacklisted_word(title: str) -> bool:
    t = _normalise(title)
    return any(bw in t for bw in _BLACKLIST)


def _is_trusted_channel(channel: str) -> bool:
    c = channel.lower()
    return any(kw in c for kw in _TRUSTED_CHANNEL_KEYWORDS)


# ── Core search ────────────────────────────────────────────────────────────────

def search_youtube(
    title: str,
    artist: str,
) -> tuple[Optional[str], list[dict]]:
    """
    Search YouTube for the best studio-recording match.

    Returns:
        (best_url, all_candidates)

    best_url is None if no confident match was found.
    all_candidates is a list of dicts sorted by score descending.
    """
    def _make_queries(t: str, a: str) -> list[str]:
        return [
            f"{a} {t} official audio",
            f"{a} {t}",
        ]

    queries = _make_queries(title, artist)

    seen_ids: set[str] = set()
    raw_results: list[dict] = []

    ydl_opts = {
        "quiet": True,
        "no_warnings": True,
        "extract_flat": True,
    }

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        for query in queries:
            try:
                info = ydl.extract_info(
                    f"ytsearch{_SEARCH_LIMIT}:{query}", download=False
                )
                for entry in info.get("entries", []):
                    vid_id = entry.get("id", "")
                    if not vid_id or vid_id in seen_ids:
                        continue
                    seen_ids.add(vid_id)
                    raw_results.append({
                        "id": vid_id,
                        "title": entry.get("title", ""),
                        "channel": entry.get("channel", ""),
                        "duration": entry.get("duration"),
                    })
            except Exception as exc:
                print(f"    [yt-dlp] Search failed for query {query!r}: {exc}")

    all_candidates: list[dict] = []
    best_url: Optional[str] = None
    best_score = -1.0

    for r in raw_results:
        vid_title = r["title"]
        channel = r["channel"]
        duration = r["duration"]
        vid_id = r["id"]
        url = f"https://www.youtube.com/watch?v={vid_id}"

        title_sim = _similarity(title, vid_title)
        # Also try matching "Artist - Title" pattern that YouTube uses
        combined_sim = _similarity(f"{artist} {title}", vid_title)
        effective_title_sim = max(title_sim, combined_sim)
        # Guard: the actual song title must have at least some standalone match
        # to prevent artist-name overlap inflating combined_sim artificially.
        # (e.g. "Calcutta Oroscopo" vs "Calcutta - Pesto" → title_sim is near 0)
        if title_sim < 0.25 and effective_title_sim == combined_sim:
            effective_title_sim = title_sim

        blacklisted = _has_blacklisted_word(vid_title)
        trusted = _is_trusted_channel(channel)

        # Score: title similarity is primary signal; trust bonus; blacklist penalty
        score = effective_title_sim
        if trusted:
            score += 0.15
        if blacklisted:
            score -= 0.30
        score = max(0.0, min(1.0, score))

        all_candidates.append({
            "title": vid_title,
            "channel": channel,
            "duration": duration,
            "title_sim": effective_title_sim,
            "trusted": trusted,
            "blacklisted": blacklisted,
            "score": score,
            "url": url,
        })

        # Accept if: title matches well AND not blacklisted, OR from trusted channel
        # with a strong title match (higher bar to avoid label-channel false positives)
        passes = (
            effective_title_sim >= _MIN_TITLE_SIMILARITY and not blacklisted
        ) or (
            trusted and effective_title_sim >= 0.7 and not blacklisted
        )

        if passes and score > best_score:
            best_score = score
            best_url = url

    all_candidates.sort(key=lambda c: c["score"], reverse=True)

    # ── Transliteration fallback ───────────────────────────────────────────────
    # If no match was found and the title/artist look Cyrillic, retry with
    # transliterated Latin versions (some artists publish under Latin titles).
    if best_url is None and _HAS_TRANSLITERATE:
        has_cyrillic = any('\u0400' <= ch <= '\u04FF' for ch in title + artist)
        if has_cyrillic:
            try:
                lat_title = _cyr_translit(title, 'ru', reversed=True)
                lat_artist = _cyr_translit(artist, 'ru', reversed=True)
            except Exception:
                lat_title, lat_artist = title, artist

            if lat_title != title or lat_artist != artist:
                print(f"    [translit] Retrying with: {lat_artist!r} — {lat_title!r}")
                lat_url, lat_candidates = search_youtube(lat_title, lat_artist)
                if lat_url:
                    return lat_url, lat_candidates

    return best_url, all_candidates


# ── Backend helpers ────────────────────────────────────────────────────────────

def fetch_songs(api_url: str) -> list[dict]:
    r = requests.get(api_url.rstrip("/") + "/api/songs", timeout=15)
    r.raise_for_status()
    return r.json()


def patch_youtube_url(api_url: str, song_id: int, youtube_url: str) -> bool:
    url = api_url.rstrip("/") + f"/api/songs/{song_id}/sources"
    try:
        r = requests.patch(url, json={"youtube_url": youtube_url}, timeout=10)
        r.raise_for_status()
        return True
    except requests.RequestException as exc:
        print(f"    [Backend] PATCH failed for song {song_id}: {exc}")
        return False


# ── Main ───────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Fill missing YouTube URLs for SingoLing songs via yt-dlp search.",
    )
    parser.add_argument(
        "--api-url",
        default="http://127.0.0.1:8000",
        help="Base URL of the SingoLing backend (default: http://127.0.0.1:8000)",
    )
    parser.add_argument(
        "--lang",
        default=None,
        help="Only process songs with this language code (e.g. ru, it)",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Also update songs that already have a YouTube URL",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Search and print results but do not write to the backend",
    )
    parser.add_argument(
        "--review",
        action="store_true",
        help="For songs with no confident match, print top candidates for inspection",
    )
    args = parser.parse_args()

    print(f"Fetching songs from {args.api_url} …")
    songs = fetch_songs(args.api_url)
    print(f"Total songs: {len(songs)}")

    to_process = [
        s for s in songs
        if (args.overwrite or not s.get("youtube_url"))
        and (args.lang is None or s.get("language_code") == args.lang)
    ]

    dry_tag = " [DRY RUN]" if args.dry_run else ""
    print(f"Songs to process: {len(to_process)}{dry_tag}\n")

    found = skipped = failed = 0

    for i, song in enumerate(to_process, 1):
        song_id = song["id"]
        title = song.get("title", "").strip()
        artist = song.get("artist", "").strip()
        total = len(to_process)
        print(f"[{i}/{total}] {artist} — {title}")

        youtube_url, candidates = search_youtube(title, artist)

        if youtube_url:
            c = next(c for c in candidates if c["url"] == youtube_url)
            print(
                f"    → Match: {c['title']!r}  channel={c['channel']!r}"
                f"  title_sim={c['title_sim']:.2f}  score={c['score']:.2f}"
            )
            print(f"      {youtube_url}")
            if not args.dry_run:
                ok = patch_youtube_url(args.api_url, song_id, youtube_url)
                if ok:
                    found += 1
                    print("    ✓ Updated")
                else:
                    failed += 1
            else:
                found += 1
        else:
            print("    ✗ No confident match found")
            if args.review and candidates:
                print(f"      Top results:")
                for c in candidates[:5]:
                    flags = []
                    if c["trusted"]:
                        flags.append("trusted-channel")
                    if c["blacklisted"]:
                        flags.append("BLACKLISTED")
                    flag_str = f"  [{', '.join(flags)}]" if flags else ""
                    print(
                        f"        {c['title']!r}  ch={c['channel']!r}"
                        f"  title_sim={c['title_sim']:.2f}  score={c['score']:.2f}{flag_str}"
                    )
                    print(f"        {c['url']}")
            elif args.review:
                print("      (no results returned)")
            skipped += 1

        if i < len(to_process):
            time.sleep(_RATE_LIMIT_S)

    print(f"\n── Summary {'─' * 44}")
    print(f"  Found / updated : {found}")
    print(f"  No match        : {skipped}")
    print(f"  Errors          : {failed}")
    if args.dry_run:
        print("  (dry-run — nothing was written)")


if __name__ == "__main__":
    main()
