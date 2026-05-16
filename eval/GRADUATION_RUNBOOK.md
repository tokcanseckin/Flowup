# Pair Graduation Runbook

> Actual steps taken when graduating `ru_de` (kaikki_1) to production.
> Use this as the checklist for every subsequent pair.
> Last updated: 2026-05-15

---

## Prerequisites

- Pair has passed eval (≥ 70% coverage, normalization verified).
- Compiled SQLite DB exists in `eval/pipelines/<src_tgt>/kaikki_1/data/`.
- `DATABASE_URL` points to the production DB (export from `backend/.env`).

```bash
export $(grep DATABASE_URL backend/.env | head -1)
source .venv/bin/activate
```

---

## Step 1 — Copy DBs to `backend/dictionaries/`

Only the compiled DBs (not raw sources) go into `backend/dictionaries/`.

```bash
mkdir -p backend/dictionaries/<src_tgt>/
cp eval/pipelines/<src_tgt>/kaikki_1/data/<src_tgt>.db backend/dictionaries/<src_tgt>/
# Copy any two-hop intermediate DBs too (ru_en, en_de, …)
cp eval/pipelines/<src_tgt>/kaikki_1/data/*.db backend/dictionaries/<src_tgt>/
ls -lh backend/dictionaries/<src_tgt>/
```

---

## Step 2 — Register the pair in `fill_word_translations.py`

Add an entry to `PAIR_REGISTRY` in `pipeline/fill_word_translations.py`:

```python
("src", "tgt"): {
    "backend": "kaikki",
    "db_candidates": [
        "backend/dictionaries/src_tgt/src_tgt.db",
    ],
},
```

Smoke-test the lookup before running on production data:

```bash
python3 -c "
import sys; sys.path.insert(0, '.'); sys.path.insert(0, 'pipeline')
from pathlib import Path
from nlp.kaikki import Lookup
lk = Lookup('src', 'tgt', Path('backend/dictionaries/src_tgt/src_tgt.db'))
for w in ['test_word1', 'test_word2']:
    print(f'{w} → {lk.lookup(w)}')
lk.close()
"
```

---

## Step 3 — Fill word definitions (dry-run first)

```bash
python3 pipeline/fill_word_translations.py --pair src_tgt --dry-run 2>&1 | head -40
```

Verify coverage looks reasonable, then run for real:

```bash
python3 pipeline/fill_word_translations.py --pair src_tgt 2>&1 | tee /tmp/fill_src_tgt.log
```

**Key properties:**
- Idempotent (skips words that already have a `src_tgt` definition row).
- On miss: writes `""` so the row exists and won't be re-attempted next run.
- Calls `_sync_song_target_langs` per song — but this **derives target_langs from
  `line_translations` rows only**, so it will reset any manually set target_langs
  to whatever line translations exist. See Step 5.

**Watch for:** `skipped: 0` on songs that should already be filled = those songs were
not processed in a prior run. Confirm completion by checking the DB after:

```bash
python3 -c "
import sys; sys.path.insert(0, '.')
from backend.database import SessionLocal
import sqlalchemy as sa
db = SessionLocal()
total = db.execute(sa.text(\"SELECT COUNT(*) FROM word_definitions WHERE target_lang='tgt'\")).scalar()
nonempty = db.execute(sa.text(\"SELECT COUNT(*) FROM word_definitions WHERE target_lang='tgt' AND definition != '' AND definition IS NOT NULL\")).scalar()
print(f'Total rows: {total}, non-empty (hits): {nonempty}')
db.close()
"
```

---

## Step 4 — Fill line translations

Uses **Argos Translate** (offline, no API key needed). DeepL is tried first if
`DEEPL_API_KEY` is set, but Argos is the standard backend for this project.

Ensure the model for the pair is installed before running:

```bash
python3 -c "
import argostranslate.package, argostranslate.translate
avail = argostranslate.translate.get_installed_languages()
src_l = next((l for l in avail if l.code == 'src'), None)
tgt_l = next((l for l in avail if l.code == 'tgt'), None)
if src_l and tgt_l and src_l.get_translation(tgt_l):
    print('Model ready.')
else:
    print('Model NOT installed — run with ARGOS_AUTO_INSTALL=1')
"
```

If the model is missing, the fill script auto-installs it with
`ARGOS_AUTO_INSTALL=1`:

```bash
ARGOS_AUTO_INSTALL=1 python3 pipeline/fill_line_translations.py --src src --tgt tgt --dry-run 2>&1 | head -30
ARGOS_AUTO_INSTALL=1 python3 pipeline/fill_line_translations.py --src src --tgt tgt 2>&1 | tee /tmp/fill_lines_src_tgt.log
```

This also updates `song.target_langs` for each song it processes.

---

## Step 5 — Update `target_langs` on songs and playlists

**⚠ Important:** `fill_word_translations._sync_song_target_langs` rewrites
`song.target_langs` based solely on existing `line_translations` rows. If word
fill runs after line fill, target_langs should be correct. If word fill runs
first (or there's a session ordering issue), you must set target_langs manually:

```bash
python3 -c "
import sys, json; sys.path.insert(0, '.')
from backend.database import SessionLocal, Song, Playlist
import sqlalchemy as sa

db = SessionLocal()

# Songs: add tgt to any song that has 'tgt' line translations
songs_with_tgt_lines = db.execute(sa.text('''
    SELECT s.id FROM songs s
    WHERE s.language_code = 'src'
    AND EXISTS (
        SELECT 1 FROM line_translations lt
        JOIN lines l ON lt.line_id = l.id
        WHERE l.song_id = s.id AND l.source IS NULL AND lt.target_lang = 'tgt'
    )
''')).scalars().all()

updated = 0
for song in db.query(Song).filter(Song.id.in_(songs_with_tgt_lines)).all():
    current = json.loads(song.target_langs or '[]')
    if 'tgt' not in current:
        current.append('tgt')
        song.target_langs = json.dumps(sorted(current))
        updated += 1
print(f'Songs updated: {updated}')

# Playlists: add tgt to Russian playlists (adjust ids as needed)
pl_updated = 0
for pl in db.query(Playlist).filter(Playlist.language_code == 'src').all():
    current = json.loads(pl.target_langs or '[]')
    if 'tgt' not in current:
        current.append('tgt')
        pl.target_langs = json.dumps(sorted(current))
        pl_updated += 1
print(f'Playlists updated: {pl_updated}')

db.commit()
db.close()
"
```

---

## Step 6 — Verify end-to-end in DB

Pick a representative song and confirm word defs and line translations are present:

```bash
python3 -c "
import sys; sys.path.insert(0, '.')
from backend.database import SessionLocal, Song, WordDefinition, Word
import sqlalchemy as sa

db = SessionLocal()
song = db.query(Song).filter(Song.id == SONG_ID).first()
print('target_langs:', song.target_langs)

default_lines = [l for l in song.lines if l.source is None]
word_ids = [w.id for l in default_lines for w in l.words]

nonempty = db.query(WordDefinition).filter(
    WordDefinition.word_id.in_(word_ids),
    WordDefinition.target_lang == 'tgt',
    WordDefinition.definition != '',
    WordDefinition.definition.isnot(None)
).count()
print(f'Words: {len(word_ids)} | tgt defs (non-empty): {nonempty}')

de_lines = db.execute(sa.text(
    \"SELECT COUNT(*) FROM line_translations lt JOIN lines l ON lt.line_id=l.id WHERE l.song_id=:sid AND lt.target_lang='tgt'\"
), {'sid': SONG_ID}).scalar()
print(f'tgt line translations: {de_lines}')
db.close()
"
```

---

## Step 7 — Deploy / restart backend

The backend reads definitions from the DB on each request (no in-memory cache for
word definitions), so no deploy is required for data-only changes. However, if
`PAIR_REGISTRY` was modified in `fill_word_translations.py` but the backend uses a
separate registry, update the backend too and restart.

---

## Lessons Learned from `ru_de`

1. **Partial runs are silent.** `fill_word_translations` processes songs ordered by
   `id`. If interrupted, later songs are simply not filled — there is no error.
   Always verify coverage across ALL songs after a run, not just from log totals:

   ```sql
   SELECT s.id, COUNT(DISTINCT wd.word_id) FILTER (WHERE wd.target_lang='tgt') AS tgt_defs
   FROM songs s
   JOIN lines l ON l.song_id=s.id AND l.source IS NULL
   JOIN words w ON w.line_id=l.id
   LEFT JOIN word_definitions wd ON wd.word_id=w.id
   WHERE s.language_code='src'
   GROUP BY s.id
   HAVING COUNT(DISTINCT wd.word_id) FILTER (WHERE wd.target_lang='tgt') = 0;
   ```

   Any row returned means that song needs to be re-run (or the full run re-started;
   the script is idempotent so already-filled songs are just skipped quickly).

2. **`_sync_song_target_langs` is destructive.** It replaces `song.target_langs`
   with exactly what is in `line_translations`. Run word fill and line fill in any
   order, then verify and patch `target_langs` as in Step 5.

3. **Two fill scripts, one `target_langs`.** Both `fill_word_translations` and
   `fill_line_translations` update `song.target_langs`. The last one to run wins.
   Always do a final check and patch (Step 5) regardless of run order.

4. **The terminal running a script can be confused with a parallel session.** If
   two fill runs overlap (e.g., one from a previous session still running), the
   second will skip everything (it sees existing rows). Check the terminal IDs.
