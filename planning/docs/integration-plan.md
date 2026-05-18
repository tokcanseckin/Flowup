# Language Pair Integration Plan

> Status: planning only — no implementation yet.  
> Last updated: 2026-05-14

---

## Core Concept

The existing English lookup (`ru→en` via OpenRussian) and the kaikki-based Turkish lookup (`ru→tr`) are the **same concept** — word-level target-language equivalents — just different slots in a shared structure. There is no special treatment for English; it is just another pair.

On the player page, the user's selected target language determines which slot is read:
- English selected → reads `word_translations["en"]` (OpenRussian, as today)
- Turkish selected → reads `word_translations["tr"]` (kaikki_1)

---

## 1. Data Model

Every per-word object gets a single `word_translations` dict. `dictionary_definition` (the old top-level key) moves into the `"en"` slot and is no longer a separate field.

```json
{
  "display_form": "старый",
  "lemma": "старый",
  "grammar": "adj, masc, nom",
  "word_translations": {
    "en": "old; ancient; aged",
    "tr": ["eski", "yaşlı"]
  }
}
```

- `"en"` is a prose string (OpenRussian output, falling back to the DeepL line translation) — format unchanged from today, just relocated.
- `"tr"` is a list of word-equivalent strings from kaikki_1 `Lookup.lookup()`.
- Other future pairs (e.g. `"de"`, `"fr"`) add more slots the same way.
- If a slot is absent the frontend shows "translation unavailable" for that language.

**Open question:** normalize all slots to `list[str]` for uniform frontend rendering, or keep `"en"` as a prose string? Normalizing means reformatting existing English data during migration.

---

## 2. DB Management in Production

Large source dumps stay in `eval/sources/` (research space, re-used for rebuilding).  
Compiled SQLite DBs for production land in `backend/dictionaries/` — gitignored, reproducible via `build_db.py`.

```
backend/dictionaries/
  ru_tr/
    ru_tr.db       ← kaikki direct lookup
    ru_en.db       ← two-hop intermediate
    ru_de.db       ← two-hop intermediate
    en_tr.db       ← two-hop intermediate
    de_tr.db       ← two-hop intermediate
  ru_de/           ← future pair, same structure
    ...
```

Eval DBs in `eval/pipelines/*/data/` remain as experiment artifacts and are not used in production.

---

## 3. Pair Registry in `generate_song_data.py`

A `DICT_PAIRS` registry mirrors `LANGUAGES`, mapping `(src, tgt)` to a provider. Both pairs register symmetrically:

```python
DICT_PAIRS = {
    ("ru", "en"): OpenRussianProvider(),       # already exists, just renamed
    ("ru", "tr"): KaikkiProvider("ru_tr"),     # new
}
```

At song ingestion time, given `--lang ru`, the pipeline iterates all registered pairs whose `src == "ru"` and populates the corresponding `word_translations` slots in one pass. No new CLI flags — automatic from the registry. A pair only activates if its DB directory is present (graceful degradation).

---

## 4. Frontend — Target Language Selector

The player page needs a **selected target language** (user preference, defaulting to `"en"`).  
When the user switches:

- The word panel reads `word_translations[selectedLang]`
- If absent, shows "translation unavailable"
- The selector only surfaces languages that have at least one song with data for that slot (or alternatively, languages listed as available by the backend)

---

## 5. Backfilling Existing Playlists

`refresh_nlp.py` gets a `--fill-pair ru_tr` mode that:

1. Queries all songs where `lang == "ru"` and `word_translations` lacks a `"tr"` key
2. For each word, calls `kaikki_lookup.lookup(lemma)` → Turkish word list
3. Merges `word_translations["tr"]` into the word blob and writes back to the DB

Properties:
- **Idempotent** — only fills missing slots, safe to re-run
- **Non-destructive** — never touches `"en"`, timing, or phonetics
- Can be run incrementally (song filter) or in bulk

---

## 6. New Playlist Ingestion

No CLI changes. `generate_song_data.py` reads `DICT_PAIRS` at startup, detects which DB directories exist, and automatically populates all available target-language slots for the song's source language in a single pass.

Adding a new language pair = drop a DB folder in `backend/dictionaries/` + register in `DICT_PAIRS`. Ingestion picks it up without modification.

---

## 7. Graduation Criteria (eval → production)

A pipeline is ready to graduate when:
- Coverage ≥ ~70% on at least 3 representative test songs
- Normalization verified (no corruption artifacts like й→и)
- Two-hop hop DBs built and smoke-tested

Graduation steps:
1. Copy compiled DBs to `backend/dictionaries/<pair>/`
2. Add entry to `DICT_PAIRS`
3. Run `refresh_nlp.py --fill-pair <pair>` for existing playlists
4. Deploy

---

## Current Status (as of 2026-05-14)

| Item | Status |
|---|---|
| `kaikki_1` base pipeline (`ru_tr.db`, 14,756 rows) | ✅ complete |
| Normalization bug fixed (й→и) | ✅ verified |
| `build_db.py` extended with `--build-hops` | ✅ complete |
| `lookup.py` extended with two-hop fallback | ✅ complete |
| `eval/sources/dewiktionary` dump | ✅ downloaded |
| `eval/sources/enwiktionary` dump | 🔲 downloading (~2.5 GB) |
| Build all 4 hop DBs (`--build-hops`) | 🔲 pending EN download |
| Re-evaluate songs 12, 49, 188 with hop DBs | 🔲 pending |
| Decide graduation threshold | 🔲 pending eval results |
| Production integration (pipeline + backend + frontend) | 🔲 not started |
