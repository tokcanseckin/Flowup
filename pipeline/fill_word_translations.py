#!/usr/bin/env python3
"""
fill_word_translations.py — Populate word_definitions with a new language pair.

Reads every word in songs that match the given source language, runs the
kaikki_1 lookup, and writes results into word_definitions(word_id, target_lang,
definition).  Existing rows for the pair are skipped by default (idempotent).

Requires DATABASE_URL environment variable (PostgreSQL).

Usage:
    export DATABASE_URL="postgresql://..."

    # Dry-run first to preview coverage
    python pipeline/fill_word_translations.py --pair ru_tr --dry-run

    # Fill all Russian songs with Turkish translations
    python pipeline/fill_word_translations.py --pair ru_tr

    # One song only
    python pipeline/fill_word_translations.py --pair ru_tr --song-id 42

    # Overwrite existing rows (re-fill)
    python pipeline/fill_word_translations.py --pair ru_tr --overwrite
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

# ── Paths ─────────────────────────────────────────────────────────────────────

REPO_ROOT = Path(__file__).parent.parent.resolve()
sys.path.insert(0, str(REPO_ROOT))

# Import backend models (requires DATABASE_URL env var)
from backend.database import SessionLocal, Song, Line, Word, WordDefinition  # type: ignore
from sqlalchemy.orm import Session

# ── Kaikki DB auto-discovery ──────────────────────────────────────────────────

def _find_kaikki_db(pair: str) -> Path | None:
    src, tgt = pair.split("_", 1)
    candidates = [
        REPO_ROOT / "backend" / "dictionaries" / pair / f"{pair}.db",
        REPO_ROOT / "eval" / "pipelines" / f"{src}_{tgt}" / "kaikki_1" / "data" / f"{pair}.db",
    ]
    for p in candidates:
        if p.exists():
            return p
    return None


# ── Pair registry ─────────────────────────────────────────────────────────────
SUPPORTED_PAIRS: dict[tuple[str, str], str] = {
    ("ru", "tr"): "ru_tr",
    # Add future pairs here, e.g.:
    # ("ru", "de"): "ru_de",
}


# ── Logging ───────────────────────────────────────────────────────────────────

def _log(msg: str) -> None:
    print(msg, flush=True)


# ── Core helpers ──────────────────────────────────────────────────────────────

def _fetch_songs(session: Session, src_lang: str, song_id: int | None) -> list[Song]:
    q = session.query(Song).filter(Song.language_code == src_lang)
    if song_id is not None:
        q = q.filter(Song.id == song_id)
    return q.order_by(Song.id).all()


def _words_for_song(session: Session, song_id: int) -> list[Word]:
    return (
        session.query(Word)
        .join(Line, Word.line_id == Line.id)
        .filter(Line.song_id == song_id)
        .order_by(Line.position, Word.key_index)
        .all()
    )


def _get_existing(session: Session, word_id: int, target_lang: str) -> WordDefinition | None:
    return (
        session.query(WordDefinition)
        .filter_by(word_id=word_id, target_lang=target_lang)
        .first()
    )


def _upsert_definition(
    session: Session,
    word_id: int,
    target_lang: str,
    definition: str,
    overwrite: bool,
    dry_run: bool,
) -> str:
    existing = _get_existing(session, word_id, target_lang)
    if existing and not overwrite:
        return "skipped"
    if dry_run:
        return "dry"
    if existing:
        existing.definition = definition
        return "updated"
    session.add(WordDefinition(word_id=word_id, target_lang=target_lang, definition=definition))
    return "inserted"


# ── Kaikki lookup mode ────────────────────────────────────────────────────────

def fill_kaikki(
    session: Session,
    src_lang: str,
    tgt_lang: str,
    db_path: Path,
    song_id: int | None,
    overwrite: bool,
    dry_run: bool,
) -> None:
    from eval.pipelines.ru_tr.kaikki_1.lookup import Lookup  # type: ignore

    _log(f"Loading kaikki Lookup from {db_path} …")
    lookup = Lookup(src_lang, tgt_lang, db_path=db_path)
    _log("  Lookup ready.")

    songs = _fetch_songs(session, src_lang, song_id)
    if not songs:
        _log(f"No songs found for language '{src_lang}'.")
        lookup.close()
        return

    total_hit = total_miss = total_skipped = 0
    t0 = time.monotonic()

    for song in songs:
        _log(f"\nSong {song.id}: {song.artist} — {song.title}")
        words = _words_for_song(session, song.id)
        hit = miss = skipped = 0

        for word in words:
            if not overwrite and _get_existing(session, word.id, tgt_lang):
                skipped += 1
                continue

            results = lookup.lookup(word.lemma)
            if not results:
                if not dry_run:
                    _upsert_definition(session, word.id, tgt_lang, "", overwrite=True, dry_run=False)
                miss += 1
                continue

            definition = json.dumps(results, ensure_ascii=False)
            action = _upsert_definition(session, word.id, tgt_lang, definition, overwrite, dry_run)
            if action != "skipped":
                hit += 1
            else:
                skipped += 1

        if not dry_run:
            session.commit()

        coverage = f"{hit/(hit+miss)*100:.0f}%" if (hit + miss) > 0 else "n/a"
        _log(f"  words: {len(words)} | hit: {hit} | miss: {miss} | skipped: {skipped} | coverage: {coverage}")
        total_hit     += hit
        total_miss    += miss
        total_skipped += skipped

    lookup.close()

    elapsed = time.monotonic() - t0
    overall = f"{total_hit/(total_hit+total_miss)*100:.1f}%" if (total_hit + total_miss) > 0 else "n/a"
    _log(f"\n{'[DRY RUN] ' if dry_run else ''}Done in {elapsed:.1f}s")
    _log(f"Total hit: {total_hit} | miss: {total_miss} | skipped: {total_skipped} | overall coverage: {overall}")


# ── CLI ───────────────────────────────────────────────────────────────────────

def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Populate word_definitions for a language pair.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument(
        "--pair",
        required=True,
        help="Language pair to fill, e.g. 'ru_tr' or 'ru_en'.",
    )
    p.add_argument(
        "--song-id",
        dest="song_id",
        type=int,
        default=None,
        help="Limit to a single song ID.",
    )
    p.add_argument(
        "--overwrite",
        action="store_true",
        help="Overwrite existing word_definitions rows (default: skip).",
    )
    p.add_argument(
        "--dry-run",
        dest="dry_run",
        action="store_true",
        help="Print what would happen without writing to the DB.",
    )
    p.add_argument(
        "--db-path",
        dest="db_path",
        default=None,
        help="Override kaikki DB path (auto-detected by default).",
    )
    return p


def main() -> None:
    args = _build_parser().parse_args()

    pair = args.pair.lower().strip()
    parts = pair.split("_", 1)
    if len(parts) != 2:
        sys.exit(f"Invalid pair '{pair}'. Expected format: src_tgt, e.g. ru_tr")

    src_lang, tgt_lang = parts

    if (src_lang, tgt_lang) not in SUPPORTED_PAIRS:
        supported = ", ".join(f"{s}_{t}" for s, t in SUPPORTED_PAIRS)
        sys.exit(f"Pair '{pair}' not in registry. Supported: {supported}")

    if dry_run := args.dry_run:
        _log("[DRY RUN MODE — no writes]")

    kaikki_db = Path(args.db_path) if args.db_path else _find_kaikki_db(pair)
    if kaikki_db is None:
        sys.exit(
            f"Kaikki DB for pair '{pair}' not found.\n"
            f"Expected at:\n"
            f"  {REPO_ROOT}/backend/dictionaries/{pair}/{pair}.db\n"
            f"  {REPO_ROOT}/eval/pipelines/{src_lang}_{tgt_lang}/kaikki_1/data/{pair}.db\n"
            f"Build it first: python -m eval.pipelines.{src_lang}_{tgt_lang}.kaikki_1.build_db"
        )

    _log(f"pair: {pair} | DB: {kaikki_db}")
    session: Session = SessionLocal()
    try:
        fill_kaikki(session, src_lang, tgt_lang, kaikki_db, args.song_id, args.overwrite, dry_run)
    finally:
        session.close()


if __name__ == "__main__":
    main()
