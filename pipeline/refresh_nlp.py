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
import time
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
from nlp.pymorphy import PyMorphyBackend  # type: ignore

# ── Config ────────────────────────────────────────────────────────────────────

DB_PATH = os.environ.get(
    "DATABASE_URL",
    str(SCRIPT_DIR.parent / "backend" / "flowup.db"),
).removeprefix("sqlite:///")


# ── Helpers ───────────────────────────────────────────────────────────────────

def _log(msg: str) -> None:
    """Print with flush so nohup/pipe sees output immediately."""
    print(msg, flush=True)


# Lazy-loaded grammar backends (no ruaccent — only pymorphy3 needed for grammar)
_morph_backends: dict[str, PyMorphyBackend] = {}


def _get_morph_backend(lang_code: str) -> PyMorphyBackend | None:
    if lang_code not in ("ru", "uk"):
        return None
    if lang_code not in _morph_backends:
        morph_lang = "uk" if lang_code == "uk" else "ru"
        backend = PyMorphyBackend(morph_lang=morph_lang, use_accent=False)
        backend.load()
        _morph_backends[lang_code] = backend
    return _morph_backends[lang_code]


def _load_dicts() -> None:
    """Pre-load all dictionary indices."""
    _load_openrussian()
    _load_italian_dict()


def _process_song(conn: sqlite3.Connection, song_id: int, dry_run: bool, grammar_only: bool = False) -> None:
    cur = conn.cursor()
    t0 = time.monotonic()

    cur.execute(
        "SELECT id, language_code, title, artist FROM songs WHERE id = ?", (song_id,)
    )
    row = cur.fetchone()
    if not row:
        _log(f"  Song {song_id} not found — skipping.")
        return

    _, lang_code, title, artist = row
    lang = LANGUAGES.get(lang_code)
    if lang is None:
        _log(f"  Song {song_id} ({title!r}): unsupported lang '{lang_code}' — skipping.")
        return

    _log(f"\nSong {song_id}: {artist} — {title} [{lang_code}]")

    # ── Fetch all lines for this song ────────────────────────────────────────
    cur.execute(
        "SELECT id, original_line, translation FROM lines WHERE song_id = ? ORDER BY position",
        (song_id,),
    )
    lines = cur.fetchall()
    if not lines:
        _log("  No lines — skipping.")
        return

    line_ids   = [r[0] for r in lines]
    orig_texts = [r[1] for r in lines]

    if grammar_only:
        # Reuse existing translations from DB
        translations = [r[2] for r in lines]
        _log(f"  Grammar-only mode: skipping translation, using {len(lines)} cached translations.")
    else:
        # ── Re-translate all lines in one batch ──────────────────────────────
        _log(f"  Translating {len(orig_texts)} lines …")
        translations = translate_batch(orig_texts, lang.deepl_code, "EN-US")
        _log(f"  Translation done ({time.monotonic() - t0:.1f}s elapsed).")

        # ── Update Line.translation ──────────────────────────────────────────
        if not dry_run:
            for line_id, new_trans in zip(line_ids, translations):
                cur.execute(
                    "UPDATE lines SET translation = ? WHERE id = ?",
                    (new_trans, line_id),
                )

    # ── Re-resolve definitions + grammar for every word ─────────────────────
    cur.execute(
        "SELECT id, lemma, line_id, display_form FROM words WHERE line_id IN (%s)"
        % ",".join("?" * len(line_ids)),
        line_ids,
    )
    words = cur.fetchall()
    total_words = len(words)

    # Build a quick map: line_id → new translation
    trans_map = dict(zip(line_ids, translations))

    # Load grammar backend for this language (ru/uk only)
    morph = _get_morph_backend(lang_code)

    _log(f"  Resolving definitions + grammar for {total_words} words …")
    LOG_EVERY = max(1, total_words // 10)  # log ~10 progress checkpoints

    for i, (word_id, lemma, line_id, display_form) in enumerate(words, 1):
        new_def = _resolve_definition(lemma, lang_code, trans_map.get(line_id, ""))
        updates: dict[str, object] = {"dictionary_definition": new_def}

        if morph is not None and display_form:
            analysis = morph.analyze_token(display_form, display_form)
            updates["grammar"] = analysis.grammar

        if not dry_run:
            set_clause = ", ".join(f"{col} = ?" for col in updates)
            cur.execute(
                f"UPDATE words SET {set_clause} WHERE id = ?",
                (*updates.values(), word_id),
            )
        if i % LOG_EVERY == 0 or i == total_words:
            _log(f"    {i}/{total_words} words done …")

    elapsed = time.monotonic() - t0
    _log(f"  ✓ {len(lines)} lines, {total_words} words updated in {elapsed:.1f}s.")

    if not dry_run:
        conn.commit()
    else:
        _log("  [dry-run] No changes written.")


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="Re-run translation + definitions for existing songs.")
    parser.add_argument("--song-id", type=int, help="Process only this song ID.")
    parser.add_argument("--lang",    type=str, help="Process only songs with this language code (e.g. ru).")
    parser.add_argument("--dry-run", action="store_true", help="Show what would change without writing to DB.")
    parser.add_argument("--grammar-only", action="store_true", help="Skip translation; only re-run grammar and definitions using existing line translations.")
    args = parser.parse_args()

    _log(f"DB: {DB_PATH}")
    _log(f"Mode: {'DRY RUN' if args.dry_run else 'LIVE'}{'  [grammar-only]' if args.grammar_only else ''}")

    _load_dicts()

    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA foreign_keys = ON")

    t_start = time.monotonic()

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

        _log(f"\nSongs to process: {len(song_ids)}")

        for idx, sid in enumerate(song_ids, 1):
            _log(f"\n[{idx}/{len(song_ids)}] Processing song {sid} …")
            _process_song(conn, sid, args.dry_run, grammar_only=args.grammar_only)

        total_elapsed = time.monotonic() - t_start
        _log(f"\n✓ All done in {total_elapsed:.1f}s.")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
