#!/usr/bin/env python3
"""
onboard_playlist.py — Full pipeline: CSV → playlist → song import →
YouTube/Apple Music URLs → word definitions → line translations → target_langs.

The CSV filename must follow the pattern:
    {difficulty}_{language}_playlist.csv
e.g.: intermediate_russian_playlist.csv, beginner_english_playlist.csv

Difficulty names are mapped to CEFR levels:
    beginner           → A1
    elementary         → A2
    intermediate       → B1
    upper_intermediate → B2
    advanced           → C1
    proficient         → C2

Usage:
    python pipeline/onboard_playlist.py \\
        --csv content_to_add/songs/intermediate_russian_playlist.csv \\
        --api-url https://singoling.com \\
        --admin-token "1.xxxx"

    # Resume after an interruption at row 12:
    python pipeline/onboard_playlist.py \\
        --csv content_to_add/songs/intermediate_russian_playlist.csv \\
        --api-url https://singoling.com \\
        --admin-token "1.xxxx" \\
        --playlist-id 7 \\
        --start-at 12

    # Skip translation fill (e.g. no PAIR_REGISTRY pair for this language yet):
    python pipeline/onboard_playlist.py \\
        --csv content_to_add/songs/beginner_italian_playlist.csv \\
        --api-url https://singoling.com \\
        --admin-token "1.xxxx" \\
        --skip-translations

Environment variables:
    FLOWUP_ADMIN_TOKEN    Fallback if --admin-token not provided
    DEEPL_API_KEY         Required for line translations
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

import requests

PIPELINE_DIR = Path(__file__).parent
REPO_ROOT = PIPELINE_DIR.parent

# Ensure repo root is on sys.path so `pipeline.*` absolute imports work whether
# this file is run as a script (python3 pipeline/onboard_playlist.py) or as a
# module (python3 -m pipeline.onboard_playlist).
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

# ── Language name → ISO 639-1 code ───────────────────────────────────────────
LANG_NAME_TO_CODE: dict[str, str] = {
    "russian":     "ru",
    "ukrainian":   "uk",
    "english":     "en",
    "spanish":     "es",
    "french":      "fr",
    "german":      "de",
    "italian":     "it",
    "portuguese":  "pt",
    "dutch":       "nl",
    "polish":      "pl",
    "swedish":     "sv",
    "turkish":     "tr",
    "japanese":    "ja",
    "chinese":     "zh",
    "korean":      "ko",
    "arabic":      "ar",
    "hebrew":      "he",
}

# ── Difficulty name → CEFR level ─────────────────────────────────────────────
DIFFICULTY_TO_CEFR: dict[str, str] = {
    "beginner":           "A1",
    "elementary":         "A2",
    "intermediate":       "B1",
    "upper_intermediate": "B2",
    "advanced":           "C1",
    "proficient":         "C2",
}

def _load_pair_registry() -> dict:
    """
    Dynamically load PAIR_REGISTRY from fill_word_translations.py so that
    onboard_playlist automatically covers every graduated language pair without
    needing a separate hardcoded list.

    Requires DATABASE_URL to be exported (pulled in transitively by the backend
    imports inside fill_word_translations.py).
    """
    try:
        from pipeline.fill_word_translations import PAIR_REGISTRY  # noqa: PLC0415
        return PAIR_REGISTRY
    except Exception as exc:
        _log(f"[warn] Could not load PAIR_REGISTRY: {exc}")
        _log("       Make sure DATABASE_URL is exported before running this script.")
        return {}

# Artist values too generic for LRCLIB lookup (mirrors import_playlist.py)
_GENERIC_KEYWORDS = {
    "traditional", "folk", "soviet", "cartoon", "film", "song",
    "romance", "cheburashka", "elektronik", "prostokvashino",
    "caucasian", "future", "kozlov", "blanter",
}


def _effective_artist(raw_artist: str) -> str:
    low = raw_artist.lower()
    if any(kw in low for kw in _GENERIC_KEYWORDS):
        return ""
    return raw_artist


def _ts() -> str:
    return datetime.now().strftime("%H:%M:%S")


def _log(msg: str) -> None:
    print(msg, flush=True)


# ── CSV filename parsing ──────────────────────────────────────────────────────

def parse_csv_filename(csv_path: Path) -> tuple[str, str]:
    """
    Parse '{difficulty}_{language}_playlist.csv' → (lang_code, cefr_level).
    e.g. 'intermediate_russian_playlist.csv' → ('ru', 'B1').
    """
    stem = csv_path.stem  # "intermediate_russian_playlist"
    parts = stem.split("_")

    # Find the language token
    lang_code: str | None = None
    lang_idx: int | None = None
    for i, part in enumerate(parts):
        if part in LANG_NAME_TO_CODE:
            lang_code = LANG_NAME_TO_CODE[part]
            lang_idx = i
            break

    if lang_code is None:
        supported = ", ".join(sorted(LANG_NAME_TO_CODE))
        sys.exit(
            f"Cannot parse language from filename '{csv_path.name}'.\n"
            f"Expected: {{difficulty}}_{{language}}_playlist.csv\n"
            f"Supported languages: {supported}"
        )

    # Difficulty is everything before the language token
    difficulty_str = "_".join(parts[:lang_idx])
    cefr = DIFFICULTY_TO_CEFR.get(difficulty_str, "")
    if not cefr:
        _log(f"[warn] Unknown difficulty '{difficulty_str}' — playlist will have no CEFR level.")

    return lang_code, cefr


# ── API helpers ───────────────────────────────────────────────────────────────

def _auth_headers(admin_token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {admin_token}"}


def create_playlist(
    api_url: str,
    admin_token: str,
    lang_code: str,
    cefr: str,
    csv_path: Path,
    dry_run: bool,
) -> int:
    """Create a new hidden playlist and return its ID."""
    # Build a human-readable name from the filename stem
    stem = csv_path.stem  # "intermediate_russian_playlist"
    name = stem.replace("_playlist", "").replace("_", " ").title()

    _log(f"\nCreating playlist: name={name!r}, language_code={lang_code}, difficulty_level={cefr or '(none)'}")

    if dry_run:
        _log("[dry-run] Would POST /api/playlists — using placeholder ID 0")
        return 0

    body: dict = {
        "name": name,
        "language_code": lang_code,
        "is_hidden": True,
        "target_langs": [],
        "song_ids": [],
    }
    if cefr:
        body["difficulty_level"] = cefr

    resp = requests.post(
        f"{api_url.rstrip('/')}/api/playlists",
        json=body,
        headers=_auth_headers(admin_token),
        timeout=30,
    )
    if not resp.ok:
        sys.exit(f"Failed to create playlist: {resp.status_code} {resp.text}")

    playlist_id: int = resp.json()["id"]
    _log(f"Created playlist ID={playlist_id}")
    return playlist_id


def update_playlist_target_langs(
    api_url: str,
    admin_token: str,
    playlist_id: int,
    target_langs: list[str],
    dry_run: bool,
) -> None:
    _log(f"\n[{_ts()}] Updating playlist {playlist_id} target_langs → {target_langs}")
    if dry_run:
        _log("[dry-run] Skipping PATCH /api/playlists/{id}")
        return
    resp = requests.patch(
        f"{api_url.rstrip('/')}/api/playlists/{playlist_id}",
        json={"target_langs": target_langs},
        headers=_auth_headers(admin_token),
        timeout=30,
    )
    if not resp.ok:
        _log(f"[warn] PATCH playlist/{playlist_id} failed: {resp.status_code} {resp.text}")


# ── Song import ───────────────────────────────────────────────────────────────

_TITLE_COL_CANDIDATES = ["song_cyrillic", "song", "title"]


def _detect_title_col(header: list[str]) -> str:
    for candidate in _TITLE_COL_CANDIDATES:
        if candidate in header:
            return candidate
    sys.exit(
        f"Cannot find a title column in CSV header {header!r}.\n"
        f"Expected one of: {_TITLE_COL_CANDIDATES}\n"
        f"Override with --title-col."
    )


def import_songs(
    csv_path: Path,
    lang_code: str,
    api_url: str,
    playlist_id: int,
    admin_token: str,
    start_at: int,
    skip_rows: set[int],
    dry_run: bool,
    title_col: str | None = None,
    target_lang: str = "",
) -> tuple[int, list[tuple[int, str, str]]]:
    """Import all CSV songs via generate_song_data.py. Returns (succeeded, failed_list)."""
    rows: list[dict] = []
    with open(csv_path, encoding="utf-8") as f:
        reader = csv.DictReader(f)
        header = reader.fieldnames or []
        if title_col is None:
            title_col = _detect_title_col(list(header))
        for row in reader:
            rows.append(row)
    _log(f"Using title column: '{title_col}'")

    total = len(rows)
    succeeded = 0
    failed: list[tuple[int, str, str]] = []

    _log(f"\n{'='*60}")
    _log(f"[{_ts()}] Importing {total} songs …")
    _log(f"{'='*60}")

    for i, row in enumerate(rows, start=1):
        if i < start_at:
            continue
        if i in skip_rows:
            _log(f"  [skip] Row {i}: in --skip list")
            continue

        title = row.get(title_col, "").strip()
        raw_artist = row.get("artist", "").strip()
        artist = _effective_artist(raw_artist)

        if not title:
            _log(f"  [skip] Row {i}: missing title")
            continue

        artist_display = artist or f"(title-only, was: {raw_artist!r})"
        _log(f"\n[{_ts()}] [{i}/{total}] {title} — {artist_display}")
        _log("-" * 60)

        if dry_run:
            _log("[dry-run] Would call generate_song_data.py")
            succeeded += 1
            continue

        # ── Pre-fetch YouTube URL so generate_song_data.py can run stable-ts
        #    forced alignment when LRCLIB only has plain (unsynced) lyrics.
        youtube_url = ""
        if artist:
            try:
                from pipeline.fill_youtube_urls import search_youtube  # noqa: PLC0415
                _log("  [YouTube] Pre-fetching URL for stable-ts fallback …")
                youtube_url, _ = search_youtube(title, artist)
                if youtube_url:
                    _log(f"  [YouTube] Found: {youtube_url}")
                else:
                    _log("  [YouTube] No match — stable-ts unavailable if LRCLIB has plain lyrics only")
            except Exception as exc:
                _log(f"  [YouTube] Pre-fetch failed ({exc}) — skipping")

        cmd = [
            sys.executable,
            str(PIPELINE_DIR / "generate_song_data.py"),
            "--lang", lang_code,
            "--artist", artist,
            "--title", title,
            "--api-url", api_url,
            "--playlist-id", str(playlist_id),
            "--admin-token", admin_token,
        ]
        if target_lang:
            cmd += ["--target-lang", target_lang]
        if youtube_url:
            cmd += ["--youtube-url", youtube_url]
        t0 = time.perf_counter()
        result = subprocess.run(cmd, cwd=str(PIPELINE_DIR))
        elapsed = time.perf_counter() - t0

        if result.returncode == 0:
            succeeded += 1
            _log(f"[{_ts()}] ✓ [{i}/{total}] OK ({elapsed:.0f}s)  "
                 f"— {succeeded} done, {len(failed)} failed")
        else:
            failed.append((i, raw_artist, title))
            _log(f"[{_ts()}] ✗ [{i}/{total}] FAIL ({elapsed:.0f}s)  "
                 f"— {succeeded} done, {len(failed)} failed")

        if i < total:
            time.sleep(3)

    return succeeded, failed


# ── Song ID snapshot helpers ─────────────────────────────────────────────────

def fetch_song_ids_by_lang(api_url: str, lang_code: str) -> set[int]:
    """Return the set of song IDs currently in the DB for a language."""
    r = requests.get(f"{api_url.rstrip('/')}/api/songs", timeout=15)
    r.raise_for_status()
    return {s["id"] for s in r.json() if s.get("language_code") == lang_code}


def fetch_songs_by_ids(api_url: str, ids: set[int]) -> list[dict]:
    """Fetch full song dicts for a set of IDs (single API call, then filter)."""
    r = requests.get(f"{api_url.rstrip('/')}/api/songs", timeout=15)
    r.raise_for_status()
    return [s for s in r.json() if s["id"] in ids]


# ── URL fill (targeted — only newly imported songs) ───────────────────────────

def fill_youtube_for_songs(api_url: str, songs: list[dict], dry_run: bool) -> None:
    """Search YouTube and patch only the given songs."""
    from pipeline.fill_youtube_urls import search_youtube, patch_youtube_url  # noqa: E402

    _log(f"\n[{_ts()}] {'='*55}")
    _log(f"[{_ts()}] Filling YouTube URLs for {len(songs)} new song(s) …")
    found = skipped = 0
    for i, song in enumerate(songs, 1):
        song_id = song["id"]
        title = song.get("title", "").strip()
        artist = song.get("artist", "").strip()
        _log(f"  [{i}/{len(songs)}] {artist} — {title}")
        if song.get("youtube_url") and not dry_run:
            _log("    → already has URL, skipping")
            skipped += 1
            continue
        youtube_url, _ = search_youtube(title, artist)
        if youtube_url:
            _log(f"    → {youtube_url}")
            if not dry_run:
                patch_youtube_url(api_url, song_id, youtube_url)
            found += 1
        else:
            _log("    → no confident match")
    _log(f"  YouTube: {found} found, {skipped} already had URL, "
         f"{len(songs) - found - skipped} no match")


def fill_apple_music_for_songs(api_url: str, songs: list[dict], dry_run: bool) -> None:
    """Search Apple Music and patch only the given songs."""
    from pipeline.fill_apple_music_urls import search_apple_music, patch_apple_music_url  # noqa: E402

    _log(f"\n[{_ts()}] {'='*55}")
    _log(f"[{_ts()}] Filling Apple Music URLs for {len(songs)} new song(s) …")
    found = skipped = 0
    for i, song in enumerate(songs, 1):
        song_id = song["id"]
        title = song.get("title", "").strip()
        artist = song.get("artist", "").strip()
        _log(f"  [{i}/{len(songs)}] {artist} — {title}")
        if song.get("apple_music_url") and not dry_run:
            _log("    → already has URL, skipping")
            skipped += 1
            continue
        apple_url, _ = search_apple_music(title, artist)
        if apple_url:
            _log(f"    → {apple_url}")
            if not dry_run:
                patch_apple_music_url(api_url, song_id, apple_url)
            found += 1
        else:
            _log("    → no confident match")
    _log(f"  Apple Music: {found} found, {skipped} already had URL, "
         f"{len(songs) - found - skipped} no match")


# ── Translation fill (targeted — per new song ID) ─────────────────────────────

def run_fill_word_translations_for_songs(
    pair: str, song_ids: list[int], dry_run: bool
) -> None:
    _log(f"\n[{_ts()}] {'='*55}")
    _log(f"[{_ts()}] Filling word definitions — pair={pair}, {len(song_ids)} song(s) …")
    for song_id in song_ids:
        cmd = [
            sys.executable, str(PIPELINE_DIR / "fill_word_translations.py"),
            "--pair", pair,
            "--song-id", str(song_id),
        ]
        if dry_run:
            cmd.append("--dry-run")
        subprocess.run(cmd, cwd=str(PIPELINE_DIR))


def run_fill_line_translations_for_songs(
    src: str, tgt: str, song_ids: list[int], dry_run: bool
) -> None:
    _log(f"\n[{_ts()}] {'='*55}")
    _log(f"[{_ts()}] Filling line translations — {src}→{tgt}, {len(song_ids)} song(s) …")
    for song_id in song_ids:
        cmd = [
            sys.executable, str(PIPELINE_DIR / "fill_line_translations.py"),
            "--src", src,
            "--tgt", tgt,
            "--song-id", str(song_id),
        ]
        if dry_run:
            cmd.append("--dry-run")
        subprocess.run(cmd, cwd=str(PIPELINE_DIR))


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    p = argparse.ArgumentParser(
        description="End-to-end: CSV → playlist → songs → URLs → translations",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument("--csv", required=True,
                   help="Path to the song CSV file")
    p.add_argument("--api-url", dest="api_url", required=True,
                   help="FlowUp backend URL (e.g. https://singoling.com)")
    p.add_argument("--admin-token", dest="admin_token",
                   default=os.environ.get("FLOWUP_ADMIN_TOKEN", ""),
                   help="Admin Bearer token (or set FLOWUP_ADMIN_TOKEN env var)")
    p.add_argument("--playlist-id", dest="playlist_id", type=int, default=None,
                   help="Use existing playlist ID instead of auto-creating one")
    p.add_argument("--lang", dest="lang_override", default=None,
                   help="Override language code (e.g. ru) instead of parsing from filename")
    p.add_argument("--start-at", dest="start_at", type=int, default=1,
                   help="Resume song import from this 1-based row number (default: 1)")
    p.add_argument("--skip", dest="skip_rows", default="",
                   help="Comma-separated 1-based row numbers to skip during import")
    p.add_argument("--skip-import", dest="skip_import", action="store_true",
                   help="Skip song import (songs already in DB; run fills only)")
    p.add_argument("--title-col", dest="title_col", default=None,
                   help="CSV column for the song title (auto-detected if omitted: "
                        "tries 'song_cyrillic', 'song', 'title')")
    p.add_argument("--skip-youtube", dest="skip_youtube", action="store_true",
                   help="Skip YouTube URL fill step")
    p.add_argument("--skip-apple", dest="skip_apple", action="store_true",
                   help="Skip Apple Music URL fill step")
    p.add_argument("--skip-translations", dest="skip_translations", action="store_true",
                   help="Skip word definitions and line translation fill steps")
    p.add_argument("--dry-run", dest="dry_run", action="store_true",
                   help="Print what would happen without writing anything")
    args = p.parse_args()

    if not args.admin_token:
        sys.exit("Admin token required: --admin-token or FLOWUP_ADMIN_TOKEN env var")

    csv_path = Path(args.csv)
    if not csv_path.exists():
        sys.exit(f"CSV file not found: {csv_path}")

    # ── Parse filename for language + difficulty ───────────────────────────────
    if args.lang_override:
        lang_code = args.lang_override
        cefr = ""
    else:
        lang_code, cefr = parse_csv_filename(csv_path)

    _log(f"\n{'='*60}")
    _log(f"CSV:        {csv_path}")
    _log(f"Language:   {lang_code}  |  CEFR: {cefr or '(none)'}")
    _log(f"API URL:    {args.api_url}")
    if args.dry_run:
        _log("Mode:       DRY RUN (no writes)")
    _log(f"{'='*60}")

    # ── Discover active pairs from fill_word_translations.PAIR_REGISTRY ──────
    registry = _load_pair_registry()
    active_pairs: list[tuple[str, str]] = [
        (src, tgt) for (src, tgt) in registry if src == lang_code
    ]
    all_target_langs = sorted({tgt for _, tgt in active_pairs})
    primary_target_lang = all_target_langs[0] if all_target_langs else ""
    pair_labels = [f"{s}_{t}" for s, t in active_pairs]
    _log(f"Active pairs for '{lang_code}' (from PAIR_REGISTRY): {pair_labels or '(none)'}")

    # ── Create or reuse playlist ───────────────────────────────────────────────
    if args.playlist_id is not None:
        playlist_id = args.playlist_id
        _log(f"\nUsing existing playlist ID={playlist_id}")
    else:
        playlist_id = create_playlist(
            api_url=args.api_url,
            admin_token=args.admin_token,
            lang_code=lang_code,
            cefr=cefr,
            csv_path=csv_path,
            dry_run=args.dry_run,
        )

    # ── Snapshot existing song IDs before import ───────────────────────────────
    ids_before: set[int] = set()
    if not args.dry_run and not args.skip_import:
        _log(f"\n[{_ts()}] Snapshotting existing '{lang_code}' song IDs …")
        ids_before = fetch_song_ids_by_lang(args.api_url, lang_code)
        _log(f"  {len(ids_before)} existing song(s) for lang='{lang_code}'")

    # ── Import songs ───────────────────────────────────────────────────────────
    if not args.skip_import:
        skip_rows = {int(x) for x in args.skip_rows.split(",") if x.strip()}
        succeeded, failed = import_songs(
            csv_path=csv_path,
            lang_code=lang_code,
            api_url=args.api_url,
            playlist_id=playlist_id,
            admin_token=args.admin_token,
            start_at=args.start_at,
            skip_rows=skip_rows,
            dry_run=args.dry_run,
            title_col=args.title_col,
            target_lang=primary_target_lang,
        )
        _log(f"\n[{_ts()}] Import summary: {succeeded} OK, {len(failed)} failed")
        if failed:
            _log("  Failed rows:")
            for num, art, tit in failed:
                _log(f"    Row {num}: {art!r} – {tit!r}")
            _log(f"  To retry: --playlist-id {playlist_id} --start-at <first_failed_row>")
    else:
        _log("\n[skip] Song import skipped (--skip-import)")

    # ── Discover newly imported song IDs ───────────────────────────────────────
    new_song_ids: list[int] = []
    new_songs: list[dict] = []
    if not args.dry_run:
        ids_after = fetch_song_ids_by_lang(args.api_url, lang_code)
        new_song_ids = sorted(ids_after - ids_before)
        if new_song_ids:
            new_songs = fetch_songs_by_ids(args.api_url, set(new_song_ids))
            _log(f"\n[{_ts()}] {len(new_song_ids)} new song(s) detected: {new_song_ids}")
        else:
            _log(f"\n[{_ts()}] No new songs detected (all may already exist in DB).")
    else:
        _log("\n[dry-run] Skipping song ID diff — fill steps will be shown as plans only")

    # ── Fill YouTube URLs (new songs only) ────────────────────────────────────
    if not args.skip_youtube:
        if args.dry_run:
            _log(f"\n[dry-run] Would fill YouTube URLs for {args.csv} new songs")
        elif new_songs:
            fill_youtube_for_songs(args.api_url, new_songs, args.dry_run)
        else:
            _log("\n[info] No new songs to fill YouTube URLs for")
    else:
        _log("\n[skip] YouTube URL fill skipped")

    # ── Fill Apple Music URLs (new songs only) ────────────────────────────────
    if not args.skip_apple:
        if args.dry_run:
            _log(f"\n[dry-run] Would fill Apple Music URLs for new songs")
        elif new_songs:
            fill_apple_music_for_songs(args.api_url, new_songs, args.dry_run)
        else:
            _log("\n[info] No new songs to fill Apple Music URLs for")
    else:
        _log("\n[skip] Apple Music URL fill skipped")

    # ── Fill word definitions + line translations (new songs only) ─────────────
    if not args.skip_translations:
        if not active_pairs:
            _log(f"\n[info] No pairs for src_lang='{lang_code}' — skipping translation fill.")
            _log(f"       Add a pair to PAIR_REGISTRY in fill_word_translations.py to enable.")
        elif args.dry_run:
            for src, tgt in active_pairs:
                _log(f"\n[dry-run] Would fill word definitions + line translations: {src}_{tgt} for new songs")
        elif not new_song_ids:
            _log("\n[info] No new songs to fill translations for")
        else:
            for src, tgt in active_pairs:
                run_fill_word_translations_for_songs(f"{src}_{tgt}", new_song_ids, args.dry_run)
                run_fill_line_translations_for_songs(src, tgt, new_song_ids, args.dry_run)
    else:
        _log("\n[skip] Translation fill skipped")

    # ── Update playlist target_langs ───────────────────────────────────────────
    if all_target_langs and not args.skip_translations:
        update_playlist_target_langs(
            api_url=args.api_url,
            admin_token=args.admin_token,
            playlist_id=playlist_id,
            target_langs=all_target_langs,
            dry_run=args.dry_run,
        )
    elif args.dry_run and all_target_langs:
        _log(f"\n[dry-run] Would PATCH playlist {playlist_id} target_langs → {all_target_langs}")

    # ── Final summary ──────────────────────────────────────────────────────────
    _log(f"\n{'='*60}")
    _log(f"[{_ts()}] All done!")
    _log(f"  Playlist ID:   {playlist_id}")
    _log(f"  Language:      {lang_code}  |  CEFR: {cefr or '(none)'}")
    _log(f"  New songs:     {len(new_song_ids)}")
    _log(f"  Target langs:  {all_target_langs or '(none — no matching pairs)'}")
    _log(f"")
    _log(f"  Next steps:")
    _log(f"    - Review playlist: GET {args.api_url.rstrip('/')}/api/playlists/{playlist_id}")
    _log(f"    - Un-hide: PATCH /api/playlists/{playlist_id} {{\"is_hidden\": false}}")
    _log(f"{'='*60}")


if __name__ == "__main__":
    main()
