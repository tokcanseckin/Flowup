#!/usr/bin/env python3
"""
cleanup_en_target_lang.py — Delete mislabeled 'en'/'EN-US' word_definitions
and line_translations for English songs created by generate_song_data.py
running without --target-lang (defaulted to EN-US).
"""
import json
import os
import sys
from sqlalchemy import create_engine, text

DATABASE_URL = os.environ.get("DATABASE_URL")
if not DATABASE_URL:
    sys.exit("DATABASE_URL not set")

engine = create_engine(DATABASE_URL)

with engine.begin() as conn:
    # ── 1. Count affected rows before deletion (for reporting) ─────────────────
    wd_count = conn.execute(text("""
        SELECT COUNT(*) FROM word_definitions wd
        JOIN words w ON wd.word_id = w.id
        JOIN lines l ON w.line_id  = l.id
        JOIN songs s ON l.song_id  = s.id
        WHERE s.language_code = 'en'
          AND wd.target_lang IN ('en', 'EN-US')
    """)).scalar()

    lt_count = conn.execute(text("""
        SELECT COUNT(*) FROM line_translations lt
        JOIN lines l ON lt.line_id = l.id
        JOIN songs s ON l.song_id  = s.id
        WHERE s.language_code = 'en'
          AND lt.target_lang IN ('en', 'EN-US')
    """)).scalar()

    print(f"word_definitions to delete: {wd_count}")
    print(f"line_translations to delete: {lt_count}")

    if wd_count == 0 and lt_count == 0:
        print("Nothing to clean up — exiting.")
        sys.exit(0)

    # ── 2. Delete mislabeled word_definitions ──────────────────────────────────
    result = conn.execute(text("""
        DELETE FROM word_definitions wd
        USING words w, lines l, songs s
        WHERE wd.word_id      = w.id
          AND w.line_id       = l.id
          AND l.song_id       = s.id
          AND s.language_code = 'en'
          AND wd.target_lang IN ('en', 'EN-US')
    """))
    print(f"Deleted {result.rowcount} word_definitions with target_lang='en'/'EN-US'")

    # ── 3. Delete useless line_translations (en→en identity) ──────────────────
    result = conn.execute(text("""
        DELETE FROM line_translations lt
        USING lines l, songs s
        WHERE lt.line_id      = l.id
          AND l.song_id       = s.id
          AND s.language_code = 'en'
          AND lt.target_lang IN ('en', 'EN-US')
    """))
    print(f"Deleted {result.rowcount} line_translations with target_lang='en'/'EN-US'")

    # ── 4. Remove 'en'/'EN-US' from songs.target_langs JSON arrays ────────────
    # target_langs is stored as a JSON text array, e.g. '["en","ru"]'
    songs = conn.execute(text("""
        SELECT id, target_langs FROM songs
        WHERE language_code = 'en'
    """)).fetchall()

    patched = 0
    for song_id, tl_raw in songs:
        try:
            tl = json.loads(tl_raw or "[]")
        except (ValueError, TypeError):
            tl = []
        cleaned = [x for x in tl if x not in ("en", "EN-US")]
        if cleaned != tl:
            conn.execute(
                text("UPDATE songs SET target_langs = :tl WHERE id = :id"),
                {"tl": json.dumps(cleaned), "id": song_id},
            )
            patched += 1

    print(f"Patched target_langs for {patched} English songs (removed 'en'/'EN-US')")

print("\nCleanup complete.")
