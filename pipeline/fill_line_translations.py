#!/usr/bin/env python3
"""
fill_line_translations.py — Populate line_translations and update songs.target_langs.

Translates every lyric line for songs matching the source language, writes
LineTranslation rows, and adds the target language to each song's target_langs.

Translation backends (tried in priority order):
  1. DeepL (DEEPL_API_KEY env var required)
  2. Argos Translate (offline, must have model installed)

Requires DATABASE_URL environment variable (PostgreSQL).

Usage:
    export DATABASE_URL="postgresql://..."
    export DEEPL_API_KEY="your-key"   # optional; Argos fallback if omitted

    # Dry-run to preview
    python pipeline/fill_line_translations.py --src ru --tgt tr --dry-run

    # All Russian songs → Turkish
    python pipeline/fill_line_translations.py --src ru --tgt tr

    # Single song only
    python pipeline/fill_line_translations.py --src ru --tgt tr --song-id 12

    # Overwrite existing translations
    python pipeline/fill_line_translations.py --src ru --tgt tr --overwrite

Language target codes (DeepL):
    tr = TR   (Turkish)
    en = EN-US
    de = DE
    fr = FR
    es = ES
    ru = RU
    it = IT
    (see https://developers.deepl.com/docs/resources/supported-languages)
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Optional

REPO_ROOT = Path(__file__).parent.parent.resolve()
sys.path.insert(0, str(REPO_ROOT))

from backend.database import SessionLocal, LineTranslation, PlaylistSong, Song, Line  # type: ignore
from sqlalchemy.orm import Session

# ── DeepL / Argos (reuse logic from generate_song_data) ──────────────────────

_DEEPL_API_KEY      = os.environ.get("DEEPL_API_KEY", "")
_DEEPL_URL          = os.environ.get("DEEPL_URL", "https://api-free.deepl.com/v2/translate")
_ARGOS_AUTO_INSTALL = os.environ.get("ARGOS_AUTO_INSTALL", "0") == "1"

# DeepL target-language codes for common pairs
_DEEPL_TARGET_CODES: dict[str, str] = {
    "tr": "TR",
    "en": "EN-US",
    "de": "DE",
    "fr": "FR",
    "es": "ES",
    "ru": "RU",
    "it": "IT",
    "pt": "PT-PT",
    "nl": "NL",
    "pl": "PL",
    "sv": "SV",
    "ja": "JA",
    "zh": "ZH",
    "ko": "KO",
    "ar": "AR",
}

# DeepL source-language codes
_DEEPL_SOURCE_CODES: dict[str, str] = {
    "ru": "RU",
    "en": "EN",
    "de": "DE",
    "fr": "FR",
    "es": "ES",
    "it": "IT",
    "pt": "PT",
    "nl": "NL",
    "pl": "PL",
    "tr": "TR",
    "uk": "UK",
    "ja": "JA",
    "zh": "ZH",
    "ko": "KO",
}


def _log(msg: str) -> None:
    print(msg, flush=True)


def _normalize_argos(code: str) -> str:
    return code.split("-")[0].lower()


def _translate_argos(texts: list[str], src: str, tgt: str) -> list[str] | None:
    s = _normalize_argos(src)
    t = _normalize_argos(tgt)
    try:
        import argostranslate.package   # type: ignore[import-untyped]
        import argostranslate.translate  # type: ignore[import-untyped]
    except Exception:
        _log("  [Argos] Not installed; skipping.")
        return None

    available = argostranslate.translate.get_installed_languages()
    sl = next((l for l in available if l.code == s), None)
    tl = next((l for l in available if l.code == t), None)

    if sl and tl:
        tr = sl.get_translation(tl)
        if tr:
            _log(f"  [Argos] Translating {len(texts)} lines ({s}->{t}) using local model.")
            return [tr.translate(x) for x in texts]

    if not _ARGOS_AUTO_INSTALL:
        _log(f"  [Argos] No model for {s}->{t}. Set ARGOS_AUTO_INSTALL=1 to auto-install.")
        return None

    try:
        _log(f"  [Argos] Auto-installing model {s}->{t} …")
        argostranslate.package.update_package_index()
        pkgs = argostranslate.package.get_available_packages()
        pkg = next((p for p in pkgs if p.from_code == s and p.to_code == t), None)
        if not pkg:
            _log(f"  [Argos] No downloadable package for {s}->{t}.")
            return None
        path = pkg.download()
        argostranslate.package.install_from_path(path)
        available = argostranslate.translate.get_installed_languages()
        sl = next((l for l in available if l.code == s), None)
        tl = next((l for l in available if l.code == t), None)
        if not sl or not tl:
            return None
        tr = sl.get_translation(tl)
        if not tr:
            return None
        _log(f"  [Argos] Auto-install successful; translating {len(texts)} lines.")
        return [tr.translate(x) for x in texts]
    except Exception as exc:
        _log(f"  [Argos] Auto-install failed: {exc}")
        return None


def _translate_batch(texts: list[str], src_lang: str, tgt_lang: str) -> list[str]:
    """Translate a batch of lines. Returns translated list in same order."""
    deepl_src = _DEEPL_SOURCE_CODES.get(src_lang, src_lang.upper())
    deepl_tgt = _DEEPL_TARGET_CODES.get(tgt_lang, tgt_lang.upper())

    if not _DEEPL_API_KEY:
        _log("  [DeepL] No API key – trying Argos fallback.")
        result = _translate_argos(texts, deepl_src, deepl_tgt)
        if result is not None:
            return result
        _log("  [DeepL] Argos unavailable – no translation produced.")
        raise RuntimeError(
            "No translation backend available. "
            "Set DEEPL_API_KEY or install an Argos model (ARGOS_AUTO_INSTALL=1)."
        )

    _log(f"  [DeepL] Translating {len(texts)} lines ({deepl_src} → {deepl_tgt}) …")
    import requests  # type: ignore[import-untyped]
    max_retries = 3
    for attempt in range(1, max_retries + 1):
        try:
            r = requests.post(
                _DEEPL_URL,
                headers={"Authorization": f"DeepL-Auth-Key {_DEEPL_API_KEY}"},
                json={"text": texts, "source_lang": deepl_src, "target_lang": deepl_tgt},
                timeout=30,
            )
            if r.status_code == 429:
                wait = 2 ** attempt
                _log(f"  [DeepL] Rate limited – waiting {wait}s (attempt {attempt}/{max_retries}) …")
                time.sleep(wait)
                continue
            r.raise_for_status()
            return [t["text"] for t in r.json()["translations"]]
        except Exception as exc:
            if attempt == max_retries:
                _log(f"  [DeepL] Failed after {max_retries} attempts: {exc} – trying Argos fallback.")
                break
            _log(f"  [DeepL] Attempt {attempt} failed: {exc} – retrying …")
            time.sleep(2 ** attempt)

    result = _translate_argos(texts, deepl_src, deepl_tgt)
    if result is not None:
        return result

    raise RuntimeError("All translation backends failed.")


# ── DB helpers ────────────────────────────────────────────────────────────────

def _fetch_songs(
    session: Session,
    src_lang: str,
    song_id: Optional[int],
    playlist_id: Optional[int] = None,
) -> list[Song]:
    if playlist_id is not None:
        # Return songs in playlist order
        rows = (
            session.query(Song)
            .join(PlaylistSong, PlaylistSong.song_id == Song.id)
            .filter(PlaylistSong.playlist_id == playlist_id)
            .filter(Song.language_code == src_lang)
            .order_by(PlaylistSong.position)
            .all()
        )
        return rows
    q = session.query(Song).filter(Song.language_code == src_lang)
    if song_id is not None:
        q = q.filter(Song.id == song_id)
    return q.order_by(Song.id).all()


def _default_lines(song: Song) -> list[Line]:
    """Return only the default (source=None) lines, ordered by position."""
    return sorted((l for l in song.lines if l.source is None), key=lambda l: l.position)


def _existing_translation(session: Session, line_id: int, tgt_lang: str) -> Optional[LineTranslation]:
    return session.query(LineTranslation).filter_by(line_id=line_id, target_lang=tgt_lang).first()


def _update_song_target_langs(session: Session, song: Song, tgt_lang: str, dry_run: bool) -> bool:
    """Add tgt_lang to song.target_langs if not already present. Returns True if changed."""
    current: list[str] = json.loads(song.target_langs or "[]")
    if tgt_lang in current:
        return False
    if not dry_run:
        current.append(tgt_lang)
        song.target_langs = json.dumps(sorted(current))
    return True


# ── Main fill logic ───────────────────────────────────────────────────────────

def fill_line_translations(
    session: Session,
    src_lang: str,
    tgt_lang: str,
    song_id: Optional[int],
    overwrite: bool,
    dry_run: bool,
    batch_size: int = 50,
    playlist_id: Optional[int] = None,
) -> None:
    songs = _fetch_songs(session, src_lang, song_id, playlist_id)
    if not songs:
        _log(f"No songs found for language '{src_lang}'.")
        return

    total_inserted = total_updated = total_skipped = 0
    songs_updated_target_langs = 0
    t0 = time.monotonic()

    for song in songs:
        _log(f"\nSong {song.id}: {song.artist} — {song.title}")
        lines = _default_lines(song)
        if not lines:
            _log("  No default lines found; skipping.")
            continue

        # Determine which lines need translation
        to_translate: list[Line] = []
        skipped = 0
        for line in lines:
            existing = _existing_translation(session, line.id, tgt_lang)
            if existing and not overwrite:
                skipped += 1
            else:
                to_translate.append(line)

        _log(f"  Lines: {len(lines)} | to translate: {len(to_translate)} | already exists: {skipped}")
        total_skipped += skipped

        if not to_translate:
            # Still ensure target_langs is updated
            changed = _update_song_target_langs(session, song, tgt_lang, dry_run)
            if changed:
                songs_updated_target_langs += 1
                _log(f"  Updated target_langs → {song.target_langs if not dry_run else '[dry]'}")
            continue

        # Translate in batches to avoid hitting request size limits
        texts = [line.original_line for line in to_translate]
        translated: list[str] = []
        for i in range(0, len(texts), batch_size):
            chunk = texts[i:i + batch_size]
            if dry_run:
                translated.extend([f"[dry-run] {t}" for t in chunk])
            else:
                translated.extend(_translate_batch(chunk, src_lang, tgt_lang))

        # Write to DB
        inserted = updated = 0
        for line, text in zip(to_translate, translated):
            if dry_run:
                _log(f"  [DRY] line {line.id}: {line.original_line[:40]!r} → {text[:40]!r}")
                inserted += 1
                continue
            existing = _existing_translation(session, line.id, tgt_lang)
            if existing:
                existing.text = text
                updated += 1
            else:
                session.add(LineTranslation(line_id=line.id, target_lang=tgt_lang, text=text))
                inserted += 1

        if not dry_run:
            # Update target_langs on the song
            changed = _update_song_target_langs(session, song, tgt_lang, dry_run)
            if changed:
                songs_updated_target_langs += 1
            session.commit()

        _log(f"  Inserted: {inserted} | Updated: {updated} | Skipped: {skipped}")
        total_inserted += inserted
        total_updated  += updated

    elapsed = time.monotonic() - t0
    prefix = "[DRY RUN] " if dry_run else ""
    _log(f"\n{prefix}Done in {elapsed:.1f}s")
    _log(f"Total inserted: {total_inserted} | updated: {total_updated} | skipped: {total_skipped}")
    _log(f"Songs with target_langs updated: {songs_updated_target_langs}")


# ── CLI ───────────────────────────────────────────────────────────────────────

def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Populate line_translations and update songs.target_langs for a language pair.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument("--src", required=True, help="Source language code (e.g. ru, it, de).")
    p.add_argument("--tgt", required=True, help="Target language code (e.g. tr, en, fr).")
    p.add_argument("--song-id", dest="song_id", type=int, default=None,
                   help="Limit to a single song ID.")
    p.add_argument("--playlist-id", dest="playlist_id", type=int, default=None,
                   help="Limit to songs in a specific playlist (by playlist ID).")
    p.add_argument("--overwrite", action="store_true",
                   help="Overwrite existing line_translations rows (default: skip).")
    p.add_argument("--dry-run", dest="dry_run", action="store_true",
                   help="Print what would happen without writing to the DB.")
    p.add_argument("--batch-size", dest="batch_size", type=int, default=50,
                   help="Number of lines per DeepL/Argos request (default: 50).")
    return p


def main() -> None:
    args = _build_parser().parse_args()

    src = args.src.lower().strip()
    tgt = args.tgt.lower().strip()

    if args.dry_run:
        _log("[DRY RUN MODE — no writes]")

    scope = f"playlist {args.playlist_id}" if args.playlist_id else (f"song {args.song_id}" if args.song_id else "all songs")
    _log(f"src: {src} | tgt: {tgt} | scope: {scope} | backend: {'DeepL' if _DEEPL_API_KEY else 'Argos'}")

    session: Session = SessionLocal()
    try:
        fill_line_translations(
            session, src, tgt,
            song_id=args.song_id,
            overwrite=args.overwrite,
            dry_run=args.dry_run,
            batch_size=args.batch_size,
            playlist_id=args.playlist_id,
        )
    finally:
        session.close()


if __name__ == "__main__":
    main()
