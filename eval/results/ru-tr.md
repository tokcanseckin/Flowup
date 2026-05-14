# Evaluation: Russian → Turkish (`ru-tr`)

## Overview

This document summarises the architecture, bugs found, and fixes applied to the Russian→Turkish word-level lookup pipeline (`eval/pipelines/ru_tr/kaikki_1/`).

---

## Pipeline Architecture (`kaikki_1`)

The lookup engine (`lookup.py`) uses a three-tier cascade for each Russian lemma, backed entirely by local SQLite databases (no network calls at query time):

```
1. Direct lookup  — ru_tr.db (kaikki.org dump), POS-filtered
   ↓ if empty
2. Lemmatize      — pymorphy3 normal_form, retry direct lookup
   ↓ if empty
3. Two-hop        — ru→en→tr  AND  ru→de→tr  (ranked by agreement)
                    with OpenRussian EN-definition fallback
```

### Tier 1 & 2 — Direct lookup (`_direct_lookup`)

Queries `data/ru_tr.db` (schema: `lemma, pos, tr_word, tr_sense`) built from the kaikki.org Russian Wiktionary JSONL dump. When a POS tag is known, the query first restricts to `AND pos = ?`; if that returns nothing, it falls back to the unfiltered query so no word is silently missed.

pymorphy3 (`_lemmatize`) reduces inflected surface forms to their dictionary headword before the second attempt (e.g. `побе́ды` → `победа`).

### Tier 3 — Two-hop fallback (`_two_hop`)

Uses four additional hop databases (`data/ru_en.db`, `data/ru_de.db`, `data/en_tr.db`, `data/de_tr.db`) all built from kaikki.org dumps.

Result ranking:
1. **agreed** — words appearing in both EN→TR and DE→TR paths (highest confidence)
2. **de_only** — DE→TR only (German→Turkish alignment is clean for Slavic concepts)
3. **en_only** — EN→TR only
4. **or_only** — OpenRussian EN definitions → EN→TR (last resort)

When the DE path is empty and a POS tag is known, all EN pivots are used (relying on POS filtering to remove noise). Without a known POS, only the first EN pivot is used to limit semantic drift.

### POS detection and filtering

`_detect_pos` uses pymorphy3 to map Russian morphological tags to kaikki POS values (`noun`, `verb`, `adj`, `adv`, …).

`_tr_pos_filter` applies Turkish morphological heuristics to two-hop results:
- **verb** source → keep only words ending in `-mak`/`-mek` (Turkish infinitive suffix)
- **noun** source → exclude words ending in `-mak`/`-mek`

### Output cleaning

`_clean` strips parenthetical and bracket annotations from raw DB entries before returning results:
- `\s*\([^)]*\)` — removes `(veraltend)`, `(figurative)`, etc.
- `\s*\[[^\]]*\]` — removes `[4, 5]` sense indices etc.

`_filter_proper_nouns` removes multi-word results where any token starts with an uppercase letter (e.g. `Fetih Suresi` leaking through the `victory` pivot).

Results are capped at `_MAX_RESULTS = 4`.

---

## Databases

| File | Rows | Description |
|---|---|---|
| `data/ru_tr.db` | ~43 k entries | kaikki.org ru→tr, schema: `lemma, pos, tr_word, tr_sense` |
| `data/ru_en.db` | ~230 k | kaikki.org ru→en hop |
| `data/ru_de.db` | ~115 k | kaikki.org ru→de hop |
| `data/en_tr.db` | ~80 k | kaikki.org en→tr hop |
| `data/de_tr.db` | ~43 k | kaikki.org de→tr hop |

---

## Test Case: Song 12 (Группа Крови — Кино)

Coverage report: `eval/pipelines/ru_tr/kaikki_1/song12_lookup.csv`

**Result: 55 / 58 unique lemmas covered (94%)**

CSV schema:

| Column | Description |
|---|---|
| `display_form` | Surface form as it appears in the lyrics |
| `lemma` | Stress-marked dictionary form from song data |
| `hit` | `1` if at least one Turkish translation found, else `0` |
| `translations` | Semicolon-separated Turkish translations (max 4) |

**Misses (hit=0):**
- `ослепи́тельный` — "dazzling" (not in any hop DB)
- `поря́дковый` — "ordinal" (not in any hop DB)
- `бы` — modal particle (not a lexical word)

---

## Bugs Found and Fixes Applied

### Fix 1 — Parenthetical noise in translations ✅

**Problem:** Raw DB entries contained annotations like `nüsha (veraltend)`, `( )`, causing garbage in output.

**Fix:** `_clean()` applies `re.sub(r'\s*\([^)]*\)', '', word)` to every result from both direct and hop lookups.

---

### Fix 2 — Bracket sense indices in translations ✅

**Problem:** Hop DB entries contained strings like `[4, 5] tetikleme` (sense index from source Wiktionary).

**Fix:** `_clean()` extended to also strip `re.sub(r'\s*\[[^\]]*\]', '', word)`. Example: `курок` → `horoz; tetik; tetikleme` (was `[4, 5] tetikleme; horoz; tetik`).

---

### Fix 3 — Verbs returning noun translations ✅

**Problem:** `ставить` (to put/place) returned `mekân; meydan; yer` (location nouns) because `ru_tr.db` had no direct entry and the two-hop path via `place` (EN pivot) returned its noun senses.

**Root cause:** When DE cross-validation was empty, EN pivots were restricted to `en_words[:1]` regardless of whether POS was known. With `src_pos=None` (wrongly), all noisy pivots were included. But the actual problem was that `_two_hop` wasn't receiving `src_pos` at all.

**Fix:** Pass `src_pos` into `_two_hop`; when POS is known and DE is empty, use all EN pivots and rely on `_tr_pos_filter` to remove non-verb results. `ставить` now returns `ayarlamak; belirlemek; canlandırmak; dizmek`.

---

### Fix 4 — Proper nouns leaking through hop path ✅

**Problem:** `победа` → `zafer; Fetih Suresi; başarım; …` — "Fetih Suresi" (Quranic surah meaning "The Conquest") appears in `en_tr.db` under `victory` because Wiktionary editors added it as a semantic equivalent. It leaked through the noun non-filter (doesn't end in `-mak/-mek`).

**Fix:** `_filter_proper_nouns()` removes any multi-word result where any token starts with an uppercase letter. Applied to two-hop results before POS filtering. `победа` now returns `zafer; başarım; galebe; galibiyet`.

---

### Fix 5 — Result count cap ✅

**Problem:** Some lemmas (e.g. `вовремя`, `номер`) returned 10–30+ translations, which is noise rather than signal.

**Fix:** `_MAX_RESULTS = 4` applied on all return paths in `lookup()`.

---

## Key Observations

- **Direct ru→tr lookup has limited coverage (~40%)** — the kaikki.org Russian→Turkish dataset is sparse compared to the EN or DE paths.
- **Two-hop via EN+DE is the workhorse** — agreement between EN and DE paths is a reliable quality signal.
- **POS filtering is essential for verbs** — without the `-mak/-mek` filter, verb lookups return a mix of nouns, adjectives, and verbs from semantically related EN pivots.
- **pymorphy3 lemmatization is critical** — inflected forms in lyrics (genitive, accusative, plural) must be reduced to normal form before DB lookup.
- **Proper noun filtering at the hop output level** is a simple but effective heuristic for removing encyclopaedic entries (surah names, place names) that appear in Wiktionary translation tables.

---

## Remaining Work

| Item | Status |
|---|---|
| Direct ru→tr coverage for `ослепительный`, `порядковый` | ⏳ Not in DB |
| `бы` (modal particle) — intentional miss | — |
| Extend pipeline to additional songs | ⏳ Pending |
