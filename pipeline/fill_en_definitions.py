#!/usr/bin/env python3
"""
Backfill English word definitions from kaikki.org (Wiktionary data).

Finds all English words in the DB where word_definitions has a placeholder
'[lemma]' definition for target_lang='RU', fetches real EN glosses and RU
translations from kaikki.org, and updates the DB.

Run on the server:
  python3 fill_en_definitions.py /opt/flowup/backend/flowup.db

Options:
  --dry-run        Print what would be updated without writing to DB
  --delay SECS     Seconds to wait between requests (default: 0.3)
  --limit N        Only process N unique lemmas (for testing)
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
import time
import urllib.error
import urllib.request
from collections import defaultdict
from typing import Optional

KAIKKI_BASE = "https://kaikki.org/dictionary/English/meaning"
PREFERRED_POS = {"verb", "noun", "adj", "adv", "pron", "det", "prep", "conj", "intj"}


def _is_clean_ru_word(word: str) -> bool:
    """Return True only for simple Cyrillic words (no phrases, no punctuation)."""
    if not word or len(word) > 25 or " " in word:
        return False
    if any(c in word for c in "()«»/\\[]"):
        return False
    return bool(re.match(r"^[\u0400-\u04ff]", word))


def fetch_kaikki(lemma: str) -> tuple[Optional[str], Optional[str]]:
    """Return (en_gloss, ru_definition) for the lemma via kaikki.org.

    en_gloss: first meaningful English sense gloss
    ru_definition: Russian translation words joined with " / "
    Returns (None, None) on 404 or error.
    """
    w = lemma.lower().strip()
    if not w or len(w) < 2:
        return None, None

    url = f"{KAIKKI_BASE}/{w[0]}/{w[:2]}/{w}.jsonl"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "FlowUp/1.0"})
        with urllib.request.urlopen(req, timeout=12) as resp:
            entries = [json.loads(line) for line in resp if line.strip()]
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None, None
        raise
    except Exception:
        return None, None

    if not entries:
        return None, None

    # Selection priority:
    # 1. Pronoun/determiner entry (function words — pron/det are the primary form)
    # 2. Content word entry (noun/verb/adj/adv)
    # 3. Any preferred POS entry
    # 4. First available entry
    function_pos = {"pron", "det"}
    content_pos = {"noun", "verb", "adj", "adv"}
    best = (
        next((e for e in entries if e.get("pos") in function_pos), None)
        or next((e for e in entries if e.get("pos") in content_pos), None)
        or next((e for e in entries if e.get("pos") in PREFERRED_POS), entries[0])
    )

    # English gloss: first non-trivial gloss from senses
    SKIP_PREFIXES = (
        "simple past", "past participle", "plural of", "present participle",
        "third-person", "archaic", "alternative", "obsolete form",
    )
    en_gloss: Optional[str] = None
    for sense in best.get("senses", []):
        for g in sense.get("glosses", []):
            if g and not any(g.lower().startswith(p) for p in SKIP_PREFIXES):
                en_gloss = g
                break
        if en_gloss:
            break
    # Fall back to any gloss if all are inflection-type
    if not en_gloss:
        for sense in best.get("senses", []):
            for g in sense.get("glosses", []):
                if g:
                    en_gloss = g
                    break
            if en_gloss:
                break

    # Russian translations
    ru_words = [
        t["word"]
        for t in best.get("translations", [])
        if t.get("lang_code") == "ru" and _is_clean_ru_word(t.get("word", ""))
    ]
    ru_str = " / ".join(ru_words[:4]) if ru_words else None

    return en_gloss, ru_str


def is_placeholder(definition: Optional[str]) -> bool:
    """Return True if the definition is a '[lemma]' style placeholder."""
    if not definition:
        return True
    s = definition.strip()
    return s.startswith("[") and s.endswith("]") and len(s) < 60


def main() -> None:
    parser = argparse.ArgumentParser(description="Backfill English word definitions from kaikki.org")
    parser.add_argument("db_path", help="Path to flowup.db SQLite file")
    parser.add_argument("--dry-run", action="store_true", help="Print changes without writing")
    parser.add_argument("--delay", type=float, default=0.3, help="Seconds between HTTP requests")
    parser.add_argument("--limit", type=int, default=0, help="Max unique lemmas to process (0 = all)")
    parser.add_argument("--recheck", action="store_true",
                        help="Re-fetch ALL words (not just placeholders) to fix bad prior translations")
    args = parser.parse_args()

    conn = sqlite3.connect(args.db_path)
    conn.row_factory = sqlite3.Row

    if args.recheck:
        # Re-process everything — needed to fix bad translations from a prior run
        rows = conn.execute("""
            SELECT w.lemma, w.id AS word_id, w.dictionary_definition, wd.id AS wd_id, wd.definition AS wd_def
            FROM word_definitions wd
            JOIN words w ON w.id = wd.word_id
            WHERE wd.target_lang = 'RU'
            ORDER BY w.lemma
        """).fetchall()
    else:
        # Only process placeholder '[lemma]' entries
        rows = conn.execute("""
        SELECT w.lemma, w.id AS word_id, w.dictionary_definition, wd.id AS wd_id, wd.definition AS wd_def
        FROM word_definitions wd
        JOIN words w ON w.id = wd.word_id
        WHERE wd.target_lang = 'RU'
          AND length(wd.definition) < 60
          AND substr(wd.definition, 1, 1) = '['
          AND substr(wd.definition, length(wd.definition)) = ']'
        ORDER BY w.lemma
    """).fetchall()

    # Group by unique lemma → list of (word_id, wd_id) pairs
    lemma_to_ids: dict[str, list[tuple[int, int, Optional[str]]]] = defaultdict(list)
    for row in rows:
        lemma_to_ids[row["lemma"]].append(
            (row["word_id"], row["wd_id"], row["dictionary_definition"])
        )

    lemmas = sorted(lemma_to_ids.keys())
    if args.limit:
        lemmas = lemmas[: args.limit]

    total = len(lemmas)
    print(f"Found {total} unique English lemmas to fill")
    if args.dry_run:
        print("(dry-run — no DB writes)\n")

    updated_en = 0
    updated_ru = 0
    missed = 0

    for i, lemma in enumerate(lemmas, 1):
        id_triples = lemma_to_ids[lemma]
        en_gloss, ru_str = fetch_kaikki(lemma)

        tag = "✓" if (en_gloss or ru_str) else "✗"
        print(
            f"[{i:4}/{total}] {tag} {lemma!r:22}"
            f" EN: {(en_gloss or '—')[:40]:40}"
            f" RU: {ru_str or '—'}"
        )

        if not args.dry_run:
            for word_id, wd_id, old_dict_def in id_triples:
                if ru_str:
                    conn.execute(
                        "UPDATE word_definitions SET definition = ? WHERE id = ?",
                        (ru_str, wd_id),
                    )
                    updated_ru += 1
                if en_gloss and is_placeholder(old_dict_def):
                    conn.execute(
                        "UPDATE words SET dictionary_definition = ? WHERE id = ?",
                        (en_gloss, word_id),
                    )
                    updated_en += 1
            conn.commit()
        else:
            if ru_str:
                updated_ru += len(id_triples)
            if en_gloss:
                updated_en += len(id_triples)
            if not ru_str and not en_gloss:
                missed += 1

        time.sleep(args.delay)

    print(f"\n{'(dry-run) ' if args.dry_run else ''}Done.")
    print(f"  word_definitions updated (RU): {updated_ru}")
    print(f"  words.dictionary_definition updated (EN): {updated_en}")
    print(f"  lemmas with no kaikki.org data: {missed}")
    conn.close()


if __name__ == "__main__":
    main()
