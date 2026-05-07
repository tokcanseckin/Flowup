#!/usr/bin/env python3
"""
refresh_nlp.py — Re-run translation and definition generation for existing songs.

Updates Line.translation and Word.dictionary_definition in-place without
touching timing, phonetic annotation, or display forms.

Usage (on the server):
    cd /opt/flowup
    .venv/bin/python pipeline/refresh_nlp.py                  # all songs
    .venv/bin/python pipeline/refresh_nlp.py --song-id 42     # one song
    .venv/bin/python pipeline/refresh_nlp.py --lang ru        # all Russian songs
    .venv/bin/python pipeline/refresh_nlp.py --dry-run        # preview only
"""
from __future__ import annotations

import argparse
import os
import sqlite3
import sys
from pathlib import Path

# Allow importing from pipeline/ whether the script is run from repo root or pipeline/
SCRIPT_DIR = Path(__file__).parent.resolve()
sys.path.insert(0, str(SCRIPT_DIR))

# Load .env from backend/ if present (for DEEPL_API_KEY etc.)
try:
    from dotenv import load_dotenv
    load_dotenv(SCRIPT_DIR.parent / "backend" / ".env")
except Exception:
    pass

from generate_song_data import (  # type: ignore
    LANGUAGES,
    translate_batch,
    _resolve_definition,
    _load_openrussian,
    _load_italian_dict,
)

# ── Config ────────────────────────────────────────────────────────────────────

DB_PATH = os.environ.get(
    "DATABASE_URL",
    str(SCRIPT_DIR.parent / "backend" / "flowup.db"),
).removeprefix("sqlite:///")


# ── Helpers ───────────────────────────────────────────────────────────────────

def _load_dicts() -> None:
    """Pre-load all dictionary indices."""
    _load_openrussian()
    _load_italian_dict()


def _process_song(conn: sqlite3.Connection, song_id: int, dry_run: bool) -> None:
    cur = conn.cursor()

    cur.execute(
        "SELECT id, language_code, title, artist FROM songs WHERE id = ?", (song_id,)
    )
    row = cur.fetchone()
    if not row:
        print(f"  Song {song_id} not found — skipping.")
        return

    _, lang_code, title, artist = row
    lang = LANGUAGES.get(lang_code)
    if lang is None:
        print(f"  Song {song_id} ({title!r}): unsupported lang '{lang_code}' — skipping.")
        return

    print(f"\nSong {song_id}: {artist} — {title} [{lang_code}]")

    # ── Fetch all lines for this song ────────────────────────────────────────
    cur.execute(
        "SELECT id, original_line FROM lines WHERE song_id = ? ORDER BY position",
        (song_id,),
    )
    lines = cur.fetchall()
    if not lines:
        print("  No lines — skipping.")
        return

    line_ids   = [r[0] for r in lines]
    orig_texts = [r[1] for r in lines]

    # ── Re-translate all lines in one batch ──────────────────────────────────
    print(f"  Translating {len(orig_texts)} lines …")
    translations = translate_batch(orig_texts, lang.deepl_code, "EN-US")

    # ── Update Line.translation ──────────────────────────────────────────────
    if not dry_run:
        for line_id, new_trans in zip(line_ids, translations):
            cur.execute(
                "UPDATE lines SET translation = ? WHERE id = ?",
                (new_trans, line_id),
            )

    # ── Re-resolve definitions for every word ────────────────────────────────
    cur.execute(
        "SELECT id, lemma, line_id FROM words WHERE line_id IN (%s)"
        % ",".join("?" * len(line_ids)),
        line_ids,
    )
    words = cur.fetchall()

    # Build a quick map: line_id → new translation
    trans_map = dict(zip(line_ids, translations))

    updated_words = 0
    for word_id, lemma, line_id in words:
        new_def = _resolve_definition(lemma, lang_code, trans_map.get(line_id, ""))
        if not dry_run:
            cur.execute(
                "UPDATE words SET dictionary_definition = ? WHERE id = ?",
                (new_def, word_id),
            )
        updated_words += 1

    print(f"  Updated {len(lines)} lines, {updated_words} words.")

    if not dry_run:
        conn.commit()
    else:
        print("  [dry-run] No changes written.")


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="Re-run translation + definitions for existing songs.")
    parser.add_argument("--song-id", type=int, help="Process only this song ID.")
    parser.add_argument("--lang",    type=str, help="Process only songs with this language code (e.g. ru).")
    parser.add_argument("--dry-run", action="store_true", help="Show what would change without writing to DB.")
    args = parser.parse_args()

    print(f"DB: {DB_PATH}")
    print(f"Mode: {'DRY RUN' if args.dry_run else 'LIVE'}")

    _load_dicts()

    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA foreign_keys = ON")

    try:
        cur = conn.cursor()

        if args.song_id:
            song_ids = [args.song_id]
        elif args.lang:
            cur.execute("SELECT id FROM songs WHERE language_code = ? ORDER BY id", (args.lang,))
            song_ids = [r[0] for r in cur.fetchall()]
        else:
            cur.execute("SELECT id FROM songs ORDER BY id")
            song_ids = [r[0] for r in cur.fetchall()]

        print(f"\nSongs to process: {len(song_ids)}")

        for sid in song_ids:
            _process_song(conn, sid, args.dry_run)

        print("\nDone.")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
