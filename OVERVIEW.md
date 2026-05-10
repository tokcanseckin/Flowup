# SingoLing — Project Overview

## Intent

SingoLing is a cross-platform language learning application that teaches Russian through music. Instead of flashcards or isolated grammar drills, the learner engages with real songs: they hear the music, read the synchronized lyrics with stress marks, and inspect any unfamiliar word on demand by pressing a number key. The goal is immersion-first vocabulary acquisition with zero friction between listening and understanding.

The long-term target is a **Progressive Web App (PWA) wrapped in Tauri** for desktop distribution. The current deliverable is a **React web prototype** that proves the core interaction loop end-to-end.

---

## Core Concept

```
Real Song  →  Pre-generated JSON  →  Synchronized Lyrics UI  →  Word Inspection
(YouTube /      (Python pipeline)       (React + Tailwind)         (keyboard 1–9)
 Apple Music)
```

The key architectural decision is **pre-generation**: all NLP processing (morphology, stress marks, grammar tags) happens offline in a Python pipeline and is baked into a static JSON file. The frontend has zero runtime NLP dependency — it only needs to sync a position counter against timestamps. This gives true zero-latency lyrics rendering regardless of network conditions.

---

## Requirements

### Requirement 1 — Audio Integration (YouTube + Apple Music)

Two playback backends are supported. Each song record stores a `youtube_url` and an Apple Music track ID; the frontend picks whichever is available.

**YouTube**
- Embed playback via the YouTube IFrame Player API (`youtube.com/iframe_api`).
- No authentication required for playback.
- Position is polled from `player.getCurrentTime()` and extrapolated locally at 100 ms for smooth lyrics sync.

**Apple Music** *(primary)*
- Uses MusicKit JS v3 (`js-cdn.music.apple.com/musickit/v3/musickit.js`).
- Requires a signed Apple Music developer token (JWT, ES256, 6-month max expiry) served from the backend.
- `MusicKit.configure()` is called once at app load; playback is started with `music.setQueue({ song: id })`.
- Position is anchored on `timeupdate` events and extrapolated at 100 ms via local timestamp arithmetic (no SDK polling in the hot path).

### Requirement 2 — Data Generation Pipeline (Python)

A CLI script (`pipeline/generate_song_data.py`) that automates production of the JSON data file:

| Step | Tool | Details |
|------|------|---------|
| Fetch lyrics | `requests` + LRCLIB API | Free, no auth. Searches `lrclib.net/api/search` for time-synced LRC. |
| Parse timestamps | stdlib `re` | Converts `[mm:ss.xx]` → `start_time_ms`. End time = next line's start; last line + 4 s. |
| Translate | DeepL API | Full-line Russian → English. Mocked with a prefix string when `DEEPL_API_KEY` is absent. |
| Stress marks | `ruaccent` | Runs on the **complete line** for context-aware omograph disambiguation (e.g. за́мок vs замо́к). |
| Morphology | `pymorphy3` | Per-word: lemma, POS, case, gender, number, tense, person. |
| Dictionary | (mocked) | `dictionary_definition` field is a placeholder; structure is preserved for a real API. |

**Required output schema:**
```jsonc
{
  "youtube_url": "https://www.youtube.com/watch?v=…",
  "apple_music_id": "…",
  "title": "Song Title",
  "lines": [
    {
      "start_time_ms": 15300,
      "end_time_ms": 18000,
      "original_line": "Я люблю эту песню",
      "stressed_line": "Я люблю́ э́ту пе́сню",
      "translation": "I love this song",
      "words": [
        {
          "key": 1,
          "inflected_stressed": "люблю́",
          "lemma_stressed": "люби́ть",
          "grammar": "Verb, Present, 1st Person, Singular",
          "dictionary_definition": "to love"
        }
        // …
      ]
    }
    // …
  ]
}
```

### Requirement 3 — React Frontend

| Deliverable | Specification |
|-------------|--------------|
| **Scaffold** | Vite + React 18 + TypeScript + TailwindCSS |
| **Auth view** | Full-screen card with **Sign in with Apple** button; on success stores the Apple ID JWT in `localStorage` |
| **`AppleMusicPlayer` component** | Configures MusicKit JS v3 with a backend-issued developer token; extrapolates position at 100 ms via local timestamp arithmetic |
| **`YouTubePlayer` component** | Embeds IFrame Player API; polls `getCurrentTime()` anchored to a local timestamp for smooth sync |
| **Player controls** | Album art, track name/artist, seekable progress bar, Play/Pause button |
| **`LyricsPlayer` component** | Imports `song_data.json`; teleprompter-style tape (active line centred, neighbours faded and scaled by distance); active line shows individual words with superscript number badges |
| **Keyboard inspection** | `keydown` listener for keys `1`–`9`; locates the word by key, reads its DOM `getBoundingClientRect`, renders an anchored tooltip with inflected form, lemma, grammar, and definition |
| **Tooltip UX** | CSS keyframe enter/exit animations; auto-dismisses after 2.5 s; click also dismisses; suppressed inside `<input>`/`<textarea>` |
| **Design** | Dark-mode only (`#0d0d14` base); indigo/violet accent palette; Inter + JetBrains Mono typefaces; glass header; stress marks rendered via Unicode combining acute (U+0301) |

---

## File Map

```
Flowup/
├── pipeline/
│   ├── generate_song_data.py   # 5-step NLP pipeline CLI
│   └── requirements.txt        # requests, pymorphy3, ruaccent, onnxruntime
│
└── frontend/
    ├── index.html
    ├── package.json            # Vite 5, React 18, Tailwind 3, TypeScript 5
    ├── vite.config.ts
    ├── tailwind.config.js      # custom keyframes: tooltip-enter, tooltip-exit, line-pop
    ├── tsconfig.json
    └── src/
        ├── main.tsx
        ├── App.tsx             # auth screen + player shell
        ├── index.css           # Tailwind layers + stressed / glass utilities
        ├── types/
        │   └── spotify.d.ts   # vestigial — ambient SDK types, kept for reference
        ├── hooks/
        │   └── useAppleMusicPlayer.ts
        ├── components/
            ├── LyricsPlayer.tsx
            ├── AppleMusicPlayer.tsx
            └── YouTubePlayer.tsx
        └── data/
            └── song_data.json  # 13-line sample: Группа Крови — Кино
```

---

## How to Run

### Pipeline

```bash
cd pipeline
pip install -r requirements.txt

# With real translations:
DEEPL_API_KEY=your_key python generate_song_data.py --artist "Кино" --title "Группа крови"

# Without DeepL key (mock translations):
python generate_song_data.py --artist "Кино" --title "Группа крови"

# Copy output to the frontend:
cp song_data.json ../frontend/src/data/song_data.json
```

### Frontend

```bash
cd frontend
npm install
npm run dev          # http://localhost:5173
```

1. Open the app → sign in with Apple.
2. Select a song from the browser → the player loads Apple Music (or YouTube as fallback).
3. Press **Play**. Lyrics scroll automatically.
4. Press **1–9** while a line is active to inspect that word.

---

## Next Steps

### P0 — Core Polish (before any user testing)

- [ ] **Real dictionary definitions** — replace the mocked `dictionary_definition` field. Best candidates: Wiktionary API (free, comprehensive) or OpenRussian.org (structured, MIT-licensed).
- [ ] **Pipeline robustness** — handle LRCLIB misses gracefully with a manual LRC fallback file; add retry/back-off on DeepL rate limits.
- [ ] **Offset calibration** — LRCLIB timestamps sometimes drift from Spotify's internal position by ±500 ms. Add a per-track `offset_ms` field in the JSON and a UI slider to calibrate it live.

### P1 — Auth & Distribution

- [ ] **Login with Apple** — implement Sign in with Apple (OAuth 2.0 / OpenID Connect). Backend validates the Apple identity token (JWT, RS256) using Apple's public keys from `appleid.apple.com/auth/keys`. Requires an Apple Developer account, a Services ID, and an associated domain file at `/.well-known/apple-app-site-association`.
- [ ] **Apple Music developer token** — generate a signed ES256 JWT from the Apple Music key and serve it from `/api/music-token`. Rotate before expiry (max 6 months).
- [ ] **PWA manifest + service worker** — `manifest.json`, icons, `vite-plugin-pwa` for offline caching of the JSON data and pre-cached assets.
- [ ] **Tauri wrapper** — add `src-tauri/` scaffold, configure `tauri.conf.json` with the correct CSP for MusicKit JS, and produce signed `.dmg`/`.exe`/`.AppImage` builds.

### P2 — Learning Features

- [ ] **Word tracker** — every word inspected via keyboard (1–9) is persisted to `IndexedDB` (or Tauri's SQLite plugin for desktop). Each entry stores:
  - `inflected_form` and `lemma` (with stress marks)
  - `song_id`, `line_index` — where the word was encountered
  - `first_seen_at` / `last_seen_at` timestamps
  - `lookup_count` — how many times the user has inspected this word
  A **"My Words"** screen lists the accumulated vocabulary, filterable by song and sortable by frequency or recency.
- [ ] **Spaced repetition (SRS)** — integrate SM-2 or FSRS for scheduled word review sessions between listening sessions.
- [ ] **Romanization toggle** — optionally show a Cyrillic-to-Latin transliteration below the active line for absolute beginners.
- [ ] **Difficulty filter** — tag words by CEFR level (A1–C2) using a Russian frequency wordlist and let learners highlight only words above their level.

### P3 — Content & Scale

- [ ] **Song browser** — UI to search and queue tracks (YouTube search or Apple Music catalog API), then auto-runs the pipeline on demand (or checks a pre-built cache).
- [ ] **Batch pipeline** — extend `generate_song_data.py` to accept a playlist URI and process all tracks, writing one JSON file per track into a `data/` directory.
- [ ] **Community corrections** — crowdsource stress-mark and definition corrections via a simple GitHub-backed PR flow or a lightweight editor UI.

### P4 — Mobile

- [ ] **Responsive layout** — current layout is desktop-first; adapt the lyrics tape for small screens (larger touch targets, swipe to dismiss tooltip).
- [ ] **Capacitor / Tauri Mobile** — package as iOS/Android app once the PWA baseline is solid.
