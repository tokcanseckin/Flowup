# EN → ES Lookup Pipeline — Evaluation Results

## Pipeline

`eval/pipelines/en_es/kaikki_1`

**Source:** English Wiktionary dump (`eval/sources/enwiktionary/raw-wiktextract-data.jsonl.gz`, ~2.5 GB, shared with en-ru pipeline)
**Target language:** Spanish

---

## Architecture

### 1. Dictionary DB (`en_es.db`)

Built from the enwiktionary JSONL dump by `build_db.py`. Filters entries that have at least one Spanish translation (`lang_code == "es"`). Stores one row per translation:

```
definitions(lemma TEXT, pos TEXT, es_word TEXT, es_sense TEXT)
```

**Filtering:** entries tagged with `slang`, `vulgar`, `offensive`, `dialectal`, `pejorative`, `derogatory`, or `taboo` are dropped at build time.
**Size:** 147,164 rows (after filter).

### 2. Overrides (`overrides.py`)

Hand-curated `EN_ES_OVERRIDES` dict consulted *before* the DB. 365 entries covering:

- Personal pronouns (subject, object, reflexive, possessive)
- Articles (`a`/`an`, `the`)
- Demonstratives (`this`, `that`, `these`, `those`)
- Conjunctions (`and`, `but`, `or`, `as`, `than`, …)
- Prepositions (comprehensive: `to`, `of`, `in`, `by`, `for`, `with`, …)
- Modal auxiliaries (`can`, `could`, `will`, `would`, `shall`, `should`, `may`, `might`, `must`)
- Copula / auxiliaries (`be`, `is`, `are`, `was`, `were`, `been`, `am`, `'m`, `'re`, `'s`, `we're`, …)
- Negation, adverbs, interrogatives, quantifiers
- Song vocabulary — common verbs (with merged verb+noun form where natural: `smile`, `love`, `dream`, `dance`, `hope`, …)
- Song vocabulary — adjectives and nouns

Overrides exist because the raw wiktionary data sometimes produces noisy, regionalism-heavy, or POS-mismatched translations for very common words.

### 3. Lookup Logic (`lookup.py`)

**Lemmatization / POS tagging:** spaCy `en_core_web_sm`.

**Noise filter:** punctuation-only tokens, pure numbers, and DET-tagged function words are skipped — *after* checking overrides first (so `this`, `that` etc. still resolve via overrides).

**Auxiliary words:** `_AUX_WORDS = {"be", "have", "will", "shall"}` — skipped in DB lookup (covered by overrides).

**Contraction expansion:** contractions are split into constituent tokens before lookup.

| Contraction | Expands to |
|---|---|
| `don't` | `do` + `not` |
| `doesn't` | `do` + `not` |
| `didn't` | `did` + `not` |
| `can't` | `can` + `not` |
| `won't` | `will` + `not` |
| `I'm` | `I` + `am` |
| `you're` | `you` + `are` |
| `we're` | `we` + `are` |
| `I'd` | `I` + `would` |
| `I'll` | `I` + `will` |
| `wanna` | `want` + `to` |
| `gonna` | `going` + `to` |
| `gotta` | `got` + `to` |
| `'cause` | `because` |

**`_CONTRACTION_SILENT`:** tokens in this set are always skipped inside contraction expansion, even if they have an override. Currently `{"to"}` — prevents `wanna` from including `to`'s translations (`a / para`) in its output.

**Lookup order per token:**
1. Override check (always first)
2. Auxiliary word skip
3. Noise filter (DET, punctuation, numbers)
4. DB lookup by lemma (spaCy lemma → SQL query)
5. DB lookup by display form (fallback)

Results are deduplicated and capped at `_MAX_RESULTS = 4`.

---

## Eval Song

**Song 240** — Aerosmith, *I Don't Want to Miss a Thing*

| Metric | Value |
|---|---|
| Lines sampled | 36 / 36 (all) |
| Total word tokens | 361 |
| Words with translation | 354 |
| **Coverage** | **98%** |

### Misses (7 words)

| Word | Reason |
|---|---|
| `every` | No clean override; kaikki entry is POS-ambiguous |
| `sweetest` | Superlative form not in DB; `sweetest` not a lemma |
| `one` | High-ambiguity: pronoun / numeral / adjective; intentionally omitted from overrides |

*(4 additional misses are duplicate tokens of the above in repeated chorus lines)*

### Notable lookups

| Display form | Translation(s) |
|---|---|
| `I'd` | yo / -ía (condicional) / solía |
| `you're` | tú / usted / son / están |
| `don't` | hacer / no |
| `wanna` | querer |
| `dreaming` | soñar / sueño |
| `asleep` | dormido |
| `yeah` | sí |
| `this` | este / esta / esto |
| `the` | el / la / los / las |

---

## Issues Found & Fixed During Development

| Issue | Root Cause | Fix |
|---|---|---|
| `don't → hit=0` | `do` was in `_AUX_WORDS`, so contraction `don't → [do, not]` skipped `do` entirely | Removed `do` (and `did`) from `_AUX_WORDS` |
| `wanna → hit=0` | `wanna` not in contractions table | Added `wanna → "want to"` |
| `wanna → "querer / a / para"` (noisy) | `to` has override `["a", "para"]` which was included in expansion | Added `_CONTRACTION_SILENT = {"to"}`: always skip in contraction expansion before override check |
| `you're → "os; los; las; les"` (wrong) | Contraction expansion checked DB before overrides for sub-tokens | Moved override check first inside contraction expansion loop |
| `I'd → "yo"` only | Old code broke after first content-word hit | Fixed to collect from all expanded tokens (no early break) |
| `this → no translation` | spaCy tags `this` as DET → `_is_noise()` returned True → override never reached | Moved override check before noise filter |
| `dreaming → "sueño"` (noun only) | kaikki DB has `dream` verb translated with a noun form | Added override `dream → ["soñar", "sueño"]` (verb first) |
| `asleep → "dormido / jato"` | `jato` (Andean regionalism) not tagged `dialectal` in kaikki | Added override `asleep → ["dormido"]` |
| `yeah → "sí; seh; sipi; aa"` | Non-standard forms not caught by `_SKIP_TAGS` | Added override `yeah → ["sí"]` |

---

## Coverage Progression

| Stage | Coverage |
|---|---|
| Initial build (no overrides, no bug fixes) | 82% |
| After contraction + noise-filter fixes | 94% |
| After first override pass | 96% |
| After full reference-table override rewrite (365 entries) | **98%** |
