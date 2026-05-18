# Analytics Events Reference

Complete list of Plausible analytics events for SingoLing.

## Authentication & Onboarding

- `Sign Up` — User creates account (props: method)
- `Login` — User signs in (props: method)
- `Logout` — User signs out
- `Tutorial Started` — First-run tutorial begins
- `Tutorial Completed` — User finishes tutorial
- `Tutorial Skipped` — User dismisses tutorial early

## Song Playback

- `Song Started` — Playback begins (props: song_id, source, source_lang, target_lang)
- `Song Completed` — Song plays to end or past 90% (props: duration, completion_pct)
- `Song Abandoned` — User stops before 25% mark (props: seconds_played, abandonment_pct)
- `Playback Source Switched` — User toggles YouTube ↔ Apple Music (props: from, to)
- `Next Song` — User advances to next track (props: trigger)
- `Previous Song` — User goes back to previous track

## Word Inspection

- `Word Inspected` — User views word definition (props: method, word_index, is_stop_word)
- `Line Translated` — User views full line translation (props: method)
- `Inspect Auto-Dismissed` — Tooltip closes after 2.5s timeout
- `Word Reported` — User reports incorrect definition/grammar (props: issue_type)

## Navigation & Discovery

- `Browse Opened` — User navigates to playlist browse page
- `Playlist Selected` — User clicks into a playlist (props: playlist_id, difficulty)
- `Song Selected From Playlist` — User picks song from list (props: song_id, position_in_playlist)

## Settings

- `Settings Changed` — User modifies preference (props: setting)
- `Target Language Changed` — User switches translation language (props: from, to)
- `Apple Music Connected` — User authorizes Apple Music
- `Apple Music Disconnected` — User revokes Apple Music access

## Support

- `Help Button Clicked` — User opens help popover
- `Support Form Opened` — User opens support ticket form
- `Support Ticket Submitted` — User sends support request (props: category)

## Admin (internal)

- `Admin Panel Opened` — Admin accesses dashboard
- `Song Created` — Admin adds new song
- `Lyrics Edited` — Admin modifies song lyrics (props: song_id)
- `Alignment Task Queued` — Admin triggers lyrics alignment

## Errors

- `Playback Error` — Media fails to load/play (props: source, error_type)
- `YouTube Ad Detected` — Heuristic detects YT ad (props: song_id)
- `Apple Music Auth Failed` — MusicKit authorization fails
- `API Error` — Backend request fails (props: endpoint, status_code)

---

## Implementation Priority

### High Priority (Week 1)
1. `Song Started` / `Song Completed` — Core engagement metric
2. `Word Inspected` — Primary interaction
3. `Sign Up` / `Login` — Conversion funnel
4. `Playlist Selected` — Content discovery

### Medium Priority (Week 2)
5. `Song Abandoned` — Identify boring songs
6. `Target Language Changed` — Feature usage
7. `Tutorial Completed` / `Tutorial Skipped` — Onboarding funnel
8. `Help Button Clicked` — Support demand

### Low Priority
- Line translations, settings changes, admin events, detailed error tracking

---

## Key Metrics to Watch

**Red Flags:**
- `Song Abandoned` rate > 40% (poor content/sync quality)
- `Word Inspected` avg < 3 per song (broken UI or unclear feature)
- `Tutorial Skipped` > 70% (annoying onboarding)
- High `Playback Error` rate (integration issues)

**Success Signals:**
- `Song Completed` rate > 60%
- `Word Inspected` avg > 5 per song
- `Next Song` clicks (users want more content)
- `Target Language Changed` (multi-lang feature adoption)
