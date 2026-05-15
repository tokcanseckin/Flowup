#!/usr/bin/env python3
"""
fill_word_translations.py — Populate word_definitions with a new language pair.

Reads every word in songs that match the given source language, runs the
configured lookup backend, and writes results into word_definitions
(word_id, target_lang, definition).  Existing rows are skipped by default
(idempotent).

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

    # Override the lookup DB path (kaikki backend only)
    python pipeline/fill_word_translations.py --pair ru_tr --db-path /path/to/ru_tr.db

Adding a new pair
-----------------
Edit PAIR_REGISTRY below.  Each entry is a dict with at minimum:

    "backend": one of "kaikki" | ... (add new backends as fill_* functions below)

Backend-specific keys:
    kaikki:
        "db_candidates": list[str]  — relative repo paths tried in order
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

# ── Paths ─────────────────────────────────────────────────────────────────────

REPO_ROOT = Path(__file__).parent.parent.resolve()
PIPELINE_DIR = Path(__file__).parent.resolve()  # pipeline/
sys.path.insert(0, str(REPO_ROOT))
if str(PIPELINE_DIR) not in sys.path:
    sys.path.insert(0, str(PIPELINE_DIR))

# Import backend models (requires DATABASE_URL env var)
from backend.database import SessionLocal, Song, Line, Word, WordDefinition  # type: ignore
from sqlalchemy.orm import Session

# ── Pair registry ─────────────────────────────────────────────────────────────
#
# Each key is (src_lang, tgt_lang).  The value dict must have "backend" and
# any additional keys required by that backend's fill_* function.
#
PAIR_REGISTRY: dict[tuple[str, str], dict[str, Any]] = {
    # ── Russian → Turkish (kaikki_1, production-ready) ────────────────────────
    ("ru", "tr"): {
        "backend": "kaikki",
        "db_candidates": [
            "backend/dictionaries/ru_tr/ru_tr.db",
            "eval/pipelines/ru_tr/kaikki_1/data/ru_tr.db",
        ],
    },
    # ── Russian → German (kaikki_1) ───────────────────────────────────────────
    ("ru", "de"): {
        "backend": "kaikki",
        "db_candidates": [
            "backend/dictionaries/ru_de/ru_de.db",
            "eval/pipelines/ru_de/kaikki_1/data/ru_de.db",
        ],
    },
    # ── English → Russian (kaikki) ── stub: build en_ru.db first ─────────────
    # ("en", "ru"): {
    #     "backend": "kaikki",
    #     "db_candidates": [
    #         "backend/dictionaries/en_ru/en_ru.db",
    #     ],
    # },
    # ── Russian → English (kaikki) ── stub: build ru_en.db first ─────────────
    # ("ru", "en"): {
    #     "backend": "kaikki",
    #     "db_candidates": [
    #         "backend/dictionaries/ru_en/ru_en.db",
    #     ],
    # },
    # ── Italian → English (kaikki) ── stub: build it_en.db first ─────────────
    # ("it", "en"): {
    #     "backend": "kaikki",
    #     "db_candidates": [
    #         "backend/dictionaries/it_en/it_en.db",
    #     ],
    # },
    # ── Add further pairs here — pattern: ("src", "tgt"): { "backend": "kaikki", ... }
    # ("tr", "en"): {
    #     "backend": "wiktionary",   # implement fill_wiktionary() below
    # },
}


# ── Logging ───────────────────────────────────────────────────────────────────

def _log(msg: str) -> None:
    print(msg, flush=True)


# ── Shared DB helpers ─────────────────────────────────────────────────────────

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


def _sync_song_target_langs(session: Session, song: Song, dry_run: bool) -> None:
    """Derive and update song.target_langs from its LineTranslation rows (default lines only)."""
    seen: set[str] = set()
    for line in song.lines:
        if line.source is not None:
            continue
        for lt in line.translations:
            seen.add(lt.target_lang.lower())
    new_langs = json.dumps(sorted(seen))
    if song.target_langs != new_langs:
        if not dry_run:
            song.target_langs = new_langs
        _log(f"  target_langs: {song.target_langs} → {new_langs}")


def _run_fill_loop(
    session: Session,
    src_lang: str,
    tgt_lang: str,
    song_id: int | None,
    overwrite: bool,
    dry_run: bool,
    lookup_fn,           # callable(lemma: str) -> list[str]
    close_fn=None,       # optional cleanup callable
) -> None:
    """Generic song→word loop shared by all backends."""
    songs = _fetch_songs(session, src_lang, song_id)
    if not songs:
        _log(f"No songs found for language '{src_lang}'.")
        if close_fn:
            close_fn()
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

            results = lookup_fn(word.lemma)
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
            _sync_song_target_langs(session, song, dry_run)
            session.commit()

        coverage = f"{hit/(hit+miss)*100:.0f}%" if (hit + miss) > 0 else "n/a"
        _log(f"  words: {len(words)} | hit: {hit} | miss: {miss} | skipped: {skipped} | coverage: {coverage}")
        total_hit     += hit
        total_miss    += miss
        total_skipped += skipped

    if close_fn:
        close_fn()

    elapsed = time.monotonic() - t0
    overall = f"{total_hit/(total_hit+total_miss)*100:.1f}%" if (total_hit + total_miss) > 0 else "n/a"
    _log(f"\n{'[DRY RUN] ' if dry_run else ''}Done in {elapsed:.1f}s")
    _log(f"Total hit: {total_hit} | miss: {total_miss} | skipped: {total_skipped} | overall coverage: {overall}")


# ── Backend: kaikki ───────────────────────────────────────────────────────────

def _resolve_kaikki_db(config: dict[str, Any], db_path_override: str | None) -> Path | None:
    if db_path_override:
        return Path(db_path_override)
    for rel in config.get("db_candidates", []):
        p = REPO_ROOT / rel
        if p.exists():
            return p
    return None


def fill_kaikki(
    session: Session,
    src_lang: str,
    tgt_lang: str,
    config: dict[str, Any],
    song_id: int | None,
    overwrite: bool,
    dry_run: bool,
    db_path_override: str | None = None,
) -> None:
    db_path = _resolve_kaikki_db(config, db_path_override)
    if db_path is None:
        candidates = "\n".join(f"  {REPO_ROOT / r}" for r in config.get("db_candidates", []))
        sys.exit(
            f"Kaikki DB for '{src_lang}_{tgt_lang}' not found.\n"
            f"Expected at:\n{candidates}\n"
            f"Build it first: python -m eval.pipelines.{src_lang}_{tgt_lang}.kaikki_1.build_db"
        )

    from nlp.kaikki import Lookup  # pipeline/nlp/kaikki.py

    _log(f"Backend: kaikki | DB: {db_path}")
    lookup = Lookup(src_lang, tgt_lang, db_path=db_path)
    _log("  Lookup ready.")

    _run_fill_loop(
        session, src_lang, tgt_lang, song_id, overwrite, dry_run,
        lookup_fn=lookup.lookup,
        close_fn=lookup.close,
    )


# ── Backend dispatch ──────────────────────────────────────────────────────────

_BACKENDS = {
    "kaikki": fill_kaikki,
    # "wiktionary": fill_wiktionary,  # add here when implemented
}


# ── CLI ───────────────────────────────────────────────────────────────────────

def _build_parser() -> argparse.ArgumentParser:
    supported = ", ".join(f"{s}_{t}" for s, t in PAIR_REGISTRY)
    p = argparse.ArgumentParser(
        description="Populate word_definitions for a language pair.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument(
        "--pair",
        required=True,
        help=f"Language pair to fill. Supported: {supported}.",
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
        help="Override the lookup DB path (kaikki backend only).",
    )
    return p


def main() -> None:
    args = _build_parser().parse_args()

    pair = args.pair.lower().strip()
    parts = pair.split("_", 1)
    if len(parts) != 2:
        sys.exit(f"Invalid pair '{pair}'. Expected format: src_tgt, e.g. ru_tr")

    src_lang, tgt_lang = parts
    key = (src_lang, tgt_lang)

    if key not in PAIR_REGISTRY:
        supported = ", ".join(f"{s}_{t}" for s, t in PAIR_REGISTRY)
        sys.exit(f"Pair '{pair}' not in registry. Supported: {supported}")

    config = PAIR_REGISTRY[key]
    backend = config["backend"]

    if backend not in _BACKENDS:
        sys.exit(f"Unknown backend '{backend}' for pair '{pair}'. Add fill_{backend}() to this script.")

    dry_run = args.dry_run
    if dry_run:
        _log("[DRY RUN MODE — no writes]")

    _log(f"pair: {pair} | backend: {backend}")

    session: Session = SessionLocal()
    try:
        _BACKENDS[backend](
            session, src_lang, tgt_lang, config,
            args.song_id, args.overwrite, dry_run,
            db_path_override=args.db_path,
        )
    finally:
        session.close()


if __name__ == "__main__":
    main()
