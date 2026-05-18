# SingoLing — Progress Report
*Generated: 2026-05-16*

---

## Initial Targets

The project (`FlowUp`, later rebranded `SingoLing`) set out to build a **Russian-immersion music learning app** as a React PWA backed by a Python FastAPI server. Core requirements at inception:

1. **Audio playback** — YouTube IFrame API + Apple MusicKit JS v3, with synchronized scrolling lyrics
2. **NLP pipeline** — offline per-song JSON generation: stress marks (`ruaccent`), morphology (`pymorphy3`), word-level dictionary definitions (OpenRussian)
3. **Word inspection** — tap or number-key lookups revealing inflected form, lemma, grammar, and definition
4. **Auth** — Google + Apple Sign In
5. **Admin surface** — song creation, lyrics management, task monitoring
6. **Deployment** — single VPS at `singoling.com` (nginx + systemd + Certbot)

Stretch goals noted in IDEAS.md: dynamic album-art backgrounds, stop-word filtering, multi-language UI, multi-target-language word translations.

---

## Daily Progress

### May 3 — Foundation
- Bootstrapped the full-stack prototype: FastAPI backend, Vite/React/Tailwind frontend, pipeline CLI
- Integrated OpenRussian dictionary with live Wiktionary API fallback (GitHub mirrors 404'd)
- Made the pipeline language-agnostic; added Italian as a second source language
- Built playlist DB models + ingestion script; 8 songs seeded
- Album-art dynamic background color extraction; stop-word index filtering
- Spotify PKCE OAuth (later removed)

### May 4 — Media Backends
- Added YouTube IFrame + Apple Music (MusicKit JS v3) playback backends
- Admin UI scaffold with song CRUD endpoints

### May 5 — Rebrand + Alignment
- Rebranded FlowUp → **SingoLing**; removed Spotify entirely
- Auto-generated lyrics on admin song creation (LRCLIB first, worker alignment fallback)
- Replaced Aeneas forced-alignment with `faster-whisper`; added `stable-ts` + Demucs/VAD test harness

### May 6 — Alignment Research
- Built `stable-ts` alignment test harness to evaluate quality ceiling

### May 7 — Worker + Player Polish
- Implemented async **alignment task queue** (worker process, admin Tasks tab, API)
- `deploy.sh` with full rsync/build/restart flow; domain live at `singoling.com`
- Verb aspect (Perfective/Imperfective) badge in inspect panel
- `refresh_nlp.py` for grammar-only re-enrichment
- Apple Music: iOS `NotAllowedError` handling, buffering spinner, auto-play on next/prev
- YouTube: seek, duration, progress bar

### May 8 — UX + Pipeline Scripts
- Lyrics panel: fills full viewport, removed redundant header, always re-centers active line
- Album art displayed for both YouTube (thumbnail) and Apple Music (`nowPlayingItem`)
- Inline source switcher (YT / AM)
- YouTube ad detection (ID mismatch + short duration heuristics)
- `fill_apple_music_urls.py` and `fill_youtube_urls.py` pipeline scripts
- Multi-layer song caching (prefetch + debounce) for near-instant navigation
- Fixed `_strip_accents` to preserve `й`/`ё` while removing U+0301 stress marks

### May 12 — Auth + Browse
- `/login` and `/signup` routes with custom Google and Apple Sign In buttons
- Apple server-to-server events endpoint
- Browse: sort playlists by difficulty (Beginner → Intermediate → Advanced)
- Added Russian Beginner + Advanced playlists

### May 13 — Cleanup
- Replaced per-song translation language selector with a global UI language selector in all navbars
- Pruned unused props and callbacks

### May 14 — Russian → Turkish Dictionary
- Built `fill_word_translations.py` with `kaikki_1` `ru→tr` pipeline
- Three-tier lookup: direct → pymorphy3 lemma → two-hop (ru→en→tr + ru→de→tr) with ranking by cross-path agreement
- Wiktionary lookup engine as an additional eval source
- Fixes: bracket stripping, proper-noun filter, cap at 4 results, POS mismatch corrections
- Integrated `ru→tr` into main pipeline; stubbed multi-pair framework

### May 15 — Multi-Language Expansion + Reporting
- Fixed target-lang case normalization (`en-US` → `en`) across DB and backend
- Grammar term localization in the inspect panel (EN / TR / RU)
- **Reporting system**: DB table, API endpoints, admin Reports tab, three-dots menu in inspect panel
- `ru→de` pair: kaikki_1 pipeline, eval, fill scripts, graduation runbook
- Auth: full session/local-storage clear on logout
- UI: language-selection box colors; restrict target-lang selector to admins

### May 16 — English Source Pairs + Bug Fixes
- **`ru→es`** eval pipeline: 35 k direct + 147 k two-hop rows, **93% coverage** — registered in fill scripts
- **`en→es`** eval + production graduation: **98% coverage**, 365-entry override dict
- **`en→de`** eval + production graduation: **100% coverage** on song 240
- **`en→pt`** eval + production graduation: **96% coverage**
- Grammar: added `Comparative` localization key
- UI: "I speak" grid expanded to 4 columns for all native languages
- Fill scripts: `--min-id`/`--max-id` flags; batch `INSERT ON CONFLICT` giving **~26× speedup**
- Onboard pipeline: pre-fetch YouTube URL so `stable-ts` alignment runs on plain-lyric songs
- Chrome: fixed Cyrillic stress-mark rendering (Noto Sans font, `inline-block` spans, removed `tracking-wide`)

---

## Current State

### What Is Shipped

**Infrastructure**

| Layer | Status |
|---|---|
| Production site | ✅ `singoling.com` (HTTPS via Certbot, nginx reverse proxy, systemd) |
| Database | ✅ PostgreSQL on UPCloud (migrated from SQLite) |
| Deploy pipeline | ✅ `deploy.sh` — git commit → rsync backend → npm build → rsync frontend → restart service |
| Alignment worker | ✅ Async task queue; `faster-whisper` forced alignment; checks LRCLIB first |

**Auth**

| Feature | Detail |
|---|---|
| Google Sign In | OAuth via `google.accounts.id`; custom-styled button |
| Apple Sign In | MusicKit SDK button; server-to-server events endpoint for token revocation |
| Session management | Full localStorage + sessionStorage clear on logout |
| Admin seeding | Backend seeds admin user on startup via env vars |

**Playback**

| Feature | Detail |
|---|---|
| YouTube | IFrame API; position polled + locally extrapolated at 100 ms; seek; ad detection |
| Apple Music | MusicKit JS v3; `timeupdate` anchored extrapolation; iOS `NotAllowedError` handled; buffering spinner |
| Source switcher | Inline YT / AM toggle in player navbar; preferred source persisted in settings |
| Album art | YouTube thumbnail or MusicKit `nowPlayingItem` image; used for dynamic background color |
| Dynamic background | Dominant/average color extracted from album art; applied to lyrics panel |
| Player controls | Album art, track name/artist, seekable progress bar, play/pause, space-key toggle |

**Lyrics & Sync**

| Feature | Detail |
|---|---|
| Synchronized lyrics | Teleprompter-style tape; active line centred; neighbours faded by distance |
| Stress marks | Unicode U+0301 combining acute; Noto Sans font; `inline-block` span wrapping for Chrome |
| Line click to seek | Tapping an inactive line seeks playback to that timestamp |
| Phonetic lines | Optional romanization / phonetic field per line |
| Multi-source lyrics | Per-song lyrics variants for default / YouTube / Apple Music sources |

**Word Inspection**

| Feature | Detail |
|---|---|
| Interaction | Tap, click, or number-key (1–9); hold-to-show on mobile; auto-dismiss after 2.5 s |
| Stop-word filtering | Common pronouns, prepositions, conjunctions excluded from numbered indices (still tappable) |
| Pause on inspect | Optional; toggleable in settings |
| Inspect panel content | Inflected + stressed form, lemma, POS, grammar tags (localized EN/TR/RU), dictionary definition |
| Three-dots menu | "Report a problem" entry in inspect panel |

**Pipeline Scripts**

| Script | Purpose |
|---|---|
| `fill_word_translations.py` | Batch word-level definitions; `PAIR_REGISTRY`; `--min-id`/`--max-id`; 26× batch INSERT speedup |
| `fill_line_translations.py` | Line-level translations via Argos (offline); `--src`, `--tgt`, `--song-id`, `--playlist-id`, `--overwrite`, `--dry-run` |
| `fill_youtube_urls.py` | Resolves YouTube URLs via yt-dlp |
| `fill_apple_music_urls.py` | Resolves Apple Music track IDs; includes transliteration |
| `refresh_nlp.py` | Re-runs morphology / grammar enrichment; grammar-only mode |
| `onboard_playlist.py` | End-to-end playlist onboarding from CSV; dynamic pair registry |
| `ingest_playlist.py` / `import_playlist.py` | Playlist CSV import utilities |

**Admin Panel** (6 tabs)

| Tab | Features |
|---|---|
| Songs | Full CRUD; edit metadata (title, artist, language, YouTube URL, Apple Music ID, target langs); lyrics editor with source switcher (default / YouTube / Apple Music) per line — text, timestamps, phonetic, translation; per-line target-language translation editing; regenerate lyrics button |
| Playlists | CRUD; difficulty, description, target langs configuration |
| Users | List all users; toggle admin access; view password status |
| Tasks | Alignment task queue with status filter; manually queue new alignment tasks (YouTube URL, language, Spotify URI) |
| Localizations | CRUD for i18n strings across EN / TR / RU; search by key or text; inline edit + add new keys |
| Reports | View user-submitted problem reports with context (word/line, song) |

**Settings Page** (4 tabs)

| Tab | Features |
|---|---|
| Preferences | Preferred playback source (YT / AM); "Prioritize content words" toggle (stops-words skip numbered shortcuts); "Pause on inspect" toggle; native language picker |
| Account | Display name, email, Apple Music connection status, sign-out |
| Subscription | Placeholder (future monetization) |
| Support | Contact form (subject + message) → creates backend report; success confirmation screen |

**Content**

| Item | Detail |
|---|---|
| Russian playlists | Beginner, Intermediate, Advanced |
| English playlists | Beginner (EN source songs), Intermediate, Advanced |
| Italian | Pipeline support + batch ingestion script |
| UI languages | EN / TR / RU via i18n key system |

### Dictionary Coverage (Production)

| Pair | Coverage | Status |
|---|---|---|
| ru → en | Live (Wiktionary API) | ✅ Production |
| ru → tr | 3-tier kaikki pipeline | ✅ Integrated; **pending graduation** (not yet in `backend/dictionaries/`) |
| ru → de | kaikki_1 | ✅ Production |
| ru → es | kaikki_1, 93% | ✅ Registered |
| en → es | kaikki_1 + overrides, 98% | ✅ Production |
| en → de | kaikki_1 + overrides, 100% | ✅ Production |
| en → pt | kaikki_1 + overrides, 96% | ✅ Production |
| en → ru | Pipeline exists | ⏳ Not yet registered |

### Known Gaps / Next Work
- **`ru→tr` graduation**: eval DB exists but not copied to `backend/dictionaries/ru_tr/` — one runbook step away
- **`en→ru`** pair pipeline exists but is unregistered
- `word_translations` data model migration (prose `"en"` string → uniform `list[str]` across all pairs) is designed but not implemented (see `INTEGRATION_PLAN.md`)
- `stable-ts` alignment quality exceeds `faster-whisper` in tests but is not deployed (RAM-constrained VPS)
- Definition field in inspect panel is still sized down; IDEAS.md notes "make definition larger and more immediate" as outstanding UX item
