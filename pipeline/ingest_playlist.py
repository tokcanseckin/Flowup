#!/usr/bin/env python3
"""
FlowUp — Playlist Ingestion Helper

Fetches all tracks from a Spotify playlist and runs generate_song_data.py
on each one.

Usage:
    # Get your token from the browser console while logged into the FlowUp app:
    #   localStorage.getItem('sp_access_token')

    python ingest_playlist.py \\
        --playlist-id 2VKXRC4lsdgsPpOgsf5keX \\
        --token BQD... \\
        --lang ru \\
        --api-url http://127.0.0.1:8000

    # Skip tracks that already exist in the backend:
        --skip-existing

    # Dry-run (print track list only, don't run pipeline):
        --dry-run
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import time
from pathlib import Path

import requests

_SPOTIFY_API = "https://api.spotify.com/v1"


# ── Spotify helpers ───────────────────────────────────────────────────────────

def _headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def fetch_playlist_tracks(playlist_id: str, token: str) -> list[dict]:
    """Return all track items from a playlist, handling pagination."""
    url = f"{_SPOTIFY_API}/playlists/{playlist_id}/tracks"
    params = {"limit": 100, "offset": 0, "fields": "items(track(id,uri,name,artists,duration_ms)),next"}
    tracks: list[dict] = []

    while url:
        r = requests.get(url, headers=_headers(token), params=params, timeout=15)
        if r.status_code == 401:
            print("ERROR: Spotify token expired or invalid. Get a fresh one from the browser:")
            print("  localStorage.getItem('sp_access_token')")
            sys.exit(1)
        r.raise_for_status()
        data = r.json()
        for item in data.get("items", []):
            track = item.get("track")
            if not track or not track.get("uri", "").startswith("spotify:track:"):
                continue  # skip null / podcast episodes
            tracks.append(track)
        url = data.get("next")
        params = {}  # next URL already includes query params

    return tracks


def fetch_existing_uris(api_url: str) -> set[str]:
    """Return Spotify URIs already stored in the backend."""
    try:
        r = requests.get(api_url.rstrip("/") + "/api/songs", timeout=10)
        r.raise_for_status()
        return {s["spotify_uri"] for s in r.json() if s.get("spotify_uri")}
    except requests.RequestException as exc:
        print(f"  [Backend] Could not fetch existing songs: {exc}")
        return set()


def create_backend_playlist(
    api_url: str,
    playlist_id: str,
    name: str,
    song_ids: list[int],
    difficulty_level: str | None,
    language_code: str,
) -> None:
    """POST a Playlist record to the backend."""
    url = api_url.rstrip("/") + "/api/playlists"
    payload = {
        "spotify_playlist_id": playlist_id,
        "name": name,
        "difficulty_level": difficulty_level,
        "language_code": language_code,
        "song_ids": song_ids,
    }
    try:
        r = requests.post(url, json=payload, timeout=15)
        if r.status_code == 201:
            data = r.json()
            print(f"\n  [Backend] Playlist created (id={data['id']}, {data['song_count']} songs).")
        else:
            print(f"  [Backend] Playlist creation returned {r.status_code}: {r.text[:200]}")
    except requests.RequestException as exc:
        print(f"  [Backend] Playlist creation failed: {exc}")


# ── Pipeline runner ───────────────────────────────────────────────────────────

def run_pipeline(
    track: dict,
    lang: str,
    api_url: str,
    output_dir: Path,
    extra_args: list[str],
) -> bool:
    uri    = track["uri"]
    title  = track["name"]
    artist = track["artists"][0]["name"] if track.get("artists") else "Unknown"

    # Sanitize for filesystem
    safe = "".join(c if c.isalnum() or c in " _-" else "_" for c in f"{artist}_{title}")[:80]
    out_file = output_dir / f"{safe}.json"

    cmd = [
        sys.executable,
        str(Path(__file__).parent / "generate_song_data.py"),
        "--lang",         lang,
        "--artist",       artist,
        "--title",        title,
        "--spotify-uri",  uri,
        "--output",       str(out_file),
        *extra_args,
    ]
    if api_url:
        cmd += ["--api-url", api_url]

    print(f"\n{'─' * 60}")
    print(f"  Processing: {artist} — {title}")
    print(f"  URI:        {uri}")
    print(f"{'─' * 60}")

    result = subprocess.run(cmd, cwd=str(Path(__file__).parent))
    if result.returncode != 0:
        print(f"  ⚠  Pipeline failed for {artist} — {title} (exit {result.returncode})")
        return False
    return True


# ── CLI ───────────────────────────────────────────────────────────────────────

def main() -> None:
    p = argparse.ArgumentParser(
        description="Ingest all tracks from a Spotify playlist into FlowUp",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--playlist-id", required=True, dest="playlist_id",
                   help="Spotify playlist ID (from the URL)")
    p.add_argument("--token", required=True,
                   help="Spotify access token — get from browser: localStorage.getItem('sp_access_token')")
    p.add_argument("--lang", default="ru",
                   help="Source language code (default: ru)")
    p.add_argument("--api-url", default="http://127.0.0.1:8000", dest="api_url",
                   help="FlowUp backend URL (default: http://127.0.0.1:8000)")
    p.add_argument("--output-dir", default="playlist_output", dest="output_dir",
                   help="Directory to write per-song JSON files (default: playlist_output/)")
    p.add_argument("--skip-existing", action="store_true", dest="skip_existing",
                   help="Skip tracks whose Spotify URI is already in the backend")
    p.add_argument("--dry-run", action="store_true", dest="dry_run",
                   help="Print track list only; do not run the pipeline")
    p.add_argument("--delay", type=float, default=2.0,
                   help="Seconds to wait between tracks (default: 2)")
    p.add_argument("--offset-ms", type=int, default=0, dest="offset_ms",
                   help="Timestamp offset passed to generate_song_data.py")
    p.add_argument("--difficulty", default=None, dest="difficulty",
                   metavar="LEVEL",
                   help="CEFR difficulty level to tag the playlist (A1|A2|B1|B2|C1|C2)")
    args = p.parse_args()

    output_dir = Path(__file__).parent / args.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)

    print(f"\nFetching playlist {args.playlist_id} …")
    tracks = fetch_playlist_tracks(args.playlist_id, args.token)
    print(f"Found {len(tracks)} tracks.\n")

    existing: set[str] = set()
    if args.skip_existing and args.api_url:
        existing = fetch_existing_uris(args.api_url)
        print(f"Backend already has {len(existing)} songs.\n")

    extra_args: list[str] = []
    if args.offset_ms:
        extra_args += ["--offset-ms", str(args.offset_ms)]

    ok_count = 0
    fail_count = 0
    skipped = 0

    ingested_song_ids: list[int] = []

    for i, track in enumerate(tracks, 1):
        artist = track["artists"][0]["name"] if track.get("artists") else "Unknown"
        title  = track["name"]
        uri    = track["uri"]

        prefix = f"[{i:2}/{len(tracks)}]"

        if args.dry_run:
            print(f"{prefix} {artist} — {title}  ({uri})")
            continue

        if uri in existing:
            print(f"{prefix} SKIP (already in backend): {artist} — {title}")
            skipped += 1
            continue

        ok = run_pipeline(track, args.lang, args.api_url, output_dir, extra_args)
        if ok:
            ok_count += 1
            # Try to find the song's backend id by URI
            if args.api_url:
                try:
                    songs_r = requests.get(args.api_url.rstrip("/") + "/api/songs", timeout=10)
                    if songs_r.ok:
                        match = next((s for s in songs_r.json() if s.get("spotify_uri") == uri), None)
                        if match:
                            ingested_song_ids.append(match["id"])
                except Exception:
                    pass
        else:
            fail_count += 1

        if i < len(tracks) and not args.dry_run:
            time.sleep(args.delay)

    if not args.dry_run:
        print(f"\n{'═' * 60}")
        print(f"  Done — {ok_count} succeeded, {fail_count} failed, {skipped} skipped.")
        print(f"  JSON files in: {output_dir}")
        print(f"{'═' * 60}")

        # Create the playlist record in the backend
        if args.api_url and ingested_song_ids:
            playlist_name = f"PL – {args.playlist_id}"
            create_backend_playlist(
                api_url=args.api_url,
                playlist_id=args.playlist_id,
                name=playlist_name,
                song_ids=ingested_song_ids,
                difficulty_level=args.difficulty,
                language_code=args.lang,
            )


if __name__ == "__main__":
    main()
