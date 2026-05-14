# Evaluation: Russian → Turkish (`ru-tr`)

## Overview

This document summarises the architecture, bugs found, and fixes applied to the Russian→Turkish word-level lookup pipeline built in `eval/`.

---

## Pipeline Architecture

The lookup engine (`eval/wiktionary.py`) uses a three-tier cascade for each Russian lemma:

```
1. tr.wiktionary  (single-hop, direct)
   ↓ if empty
2. en.wiktionary  (two-hop: Russian → English glosses → Turkish)
   ↓ if empty
3. Argos          (offline NMT fallback, ru→en→tr pivot)
```

### Tier 1 — tr.wiktionary direct (`_fetch_from_tr_wikt`)

Fetches the Turkish Wiktionary page for the Russian word, locates the `==Rusça==` (Russian) section, and extracts `[[wikilink]]` targets as Turkish translation candidates.

### Tier 2 — en.wiktionary two-hop (`_fetch_via_en_wikt`)

1. **`_fetch_en_glosses`**: calls the en.wiktionary REST API to get English glosses for the Russian word (e.g. `везти` → `["to convey", "to carry (by vehicle)", "to transport"]`).
2. For each gloss term, calls **`_fetch_tr_for_en_word`**: looks up the English word on tr.wiktionary's `==İngilizce==` (English) section to find its Turkish equivalents.
3. Fallback within tier 2: **`_fetch_tr_from_english`** checks the en.wiktionary page for the English word and looks for a `==Turkish==` section (catches loanwords that exist on en.wiktionary as Turkish entries).

### Tier 3 — Argos NMT fallback (`ArgosTranslator`)

Offline neural machine translation via `argostranslate`. Uses `ru→en→tr` as a pivot chain since direct `ru→tr` packages are unavailable. The normalised Russian lemma (stress marks stripped, lowercased) is passed as input.

### Additional data sources

- **`eval/kaikki_db.py`** / **`eval/data/ru_tr.db`**: a local SQLite database built from the kaikki.org Russian dictionary dump (~800 MB JSONL). Stores Russian lemma → Turkish word/sense pairs. Used independently in `eval/lookup.py` as an alternative or complement to the wiktionary pipeline.
- **`eval/data/wikt_cache.db`**: SQLite cache for all wiktionary network calls. Cache key: `normalised_lemma|coarse_pos`.

---

## Test Case: Song 12

Lookup was run against all unique lemmas from Song 12, producing `eval/data/song12_words.csv` (55 words). The CSV schema is:

| Column | Description |
|---|---|
| `display_form` | Surface form as it appears in the lyrics |
| `lemma` | Normalised dictionary form |
| `pos` | Coarse part-of-speech |
| `full_grammar` | Full morphological analysis string |
| `tr_definitions` | Pipe-separated Turkish translations |

---

## Bugs Found and Fixes Applied

### Bug A — Argos capitalises output: `полупустой` → `['Yarısı']`

**Root cause:** Argos receives the Russian lemma `"полупустой"` (half-empty). Argos treats the hyphenated intermediate English form `"half-empty"` as an opaque token and outputs `"Yarısı"` — which is the Turkish possessive of "half" ("its half"), not "half-empty". Argos always capitalises the first word of its output, so the result is title-cased and semantically wrong.

**Status:** Root cause confirmed. Fix: lowercase the Argos output before storing (pending implementation).

---

### Bug B — Two-hop fails for verbs: `везти` → `['taşıyabilir']` ✅ Fixed

**Root cause:** `_fetch_en_glosses` returns English glosses in infinitive form with parenthetical qualifiers:

```
"to convey"
"to carry (by vehicle)"
"to deliver"
"to transport"
```

These strings were passed verbatim to `_fetch_tr_for_en_word`, which looks them up on tr.wiktionary. Turkish Wiktionary has no page titled `"to convey"` — it has `"convey"`. The lookup returned empty for all terms, so the pipeline fell through to the Argos fallback, which produced the conjugated form `"taşıyabilir"` ("can carry").

**Fix** (commit `5ef0762`, `_fetch_via_en_wikt`): before passing each gloss term to the lookup functions, strip:
1. Parenthetical qualifiers: `re.sub(r"\s*\(.*?\)", "", term).strip()`
2. The `"to "` infinitive prefix: if the term starts with `"to "`, drop the first three characters.

After stripping, `"to convey"` → `"convey"` → `['taşımak', 'iletmek', 'getirmek', 'ulaştırmak', 'ifade etmek']`. This matches the correct output from the first known-good CSV run.

---

### Bug C — English words leaking into Turkish output ✅ Fixed

**Root cause:** `_fetch_tr_from_english` used to scan the **entire** en.wiktionary page for `{{t|tr|…}}` translation templates. For common English words (e.g. `"underground"`), these templates appear in the **English** section of the page, not the Turkish section, and many source-language words (English, French, etc.) leaked into the Turkish output.

**Fix** (commit `7339cf4`, `_fetch_tr_from_english`): split the wikitext by `==` level-2 headers, find the `==Turkish==` section (case-insensitive), and extract wikilinks from **that section only**. Words without a `==Turkish==` section return `[]`. This correctly handles loanwords that do appear as Turkish entries on en.wiktionary (e.g. Russian borrowings that passed into Turkish).

---

## Key Observations

- **tr.wiktionary is the most reliable source** for common Russian nouns and adjectives that have direct Turkish entries. Coverage is high for high-frequency words.
- **The two-hop path is essential for verbs.** Russian verbs are rarely listed directly on tr.wiktionary under the `==Rusça==` section; the en.wiktionary pivot consistently finds the correct Turkish infinitives.
- **Argos is semantically unreliable for compound/hyphenated words.** It treats hyphenated tokens as unknown single units rather than decomposing them. It is also morphologically unreliable — it may produce conjugated verb forms or possessive noun forms rather than citation forms.
- **Argos always capitalises output** (first word), so any Argos result stored in the cache or CSV appears title-cased if not normalised.
- **Cache key design matters.** Using `normalised_lemma|coarse_pos` as the cache key allows the same lemma with different parts of speech (e.g. `в` as preposition vs. noun) to store separate results.

---

## Remaining Work

| Item | Status |
|---|---|
| Fix A: lowercase / validate Argos output | ⏳ Pending |
| Clear stale cache entries (`DELETE FROM cache`) | ⏳ Pending |
| Regenerate `eval/data/song12_words.csv` with all fixes | ⏳ Pending |
| Fix B: strip `"to "` prefix and parentheticals in two-hop | ✅ Done (`5ef0762`) |
| Fix C: scope `_fetch_tr_from_english` to `==Turkish==` section | ✅ Done (`7339cf4`) |
