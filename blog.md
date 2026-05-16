# SingoLing Blog

## Week 1 — From Idea to Singing Along (May 3–9, 2026)

Welcome to SingoLing! This week we went from a blank canvas to a working app that lets you learn a language the way humans actually learn — by falling in love with a song. We hooked up two of the world's biggest music libraries, **YouTube** and **Apple Music**, so you can press play on the real recording and watch the lyrics scroll in perfect sync. Every line is timed, every syllable marked with the correct Russian stress, and every word is just a tap (or a number-key press) away from revealing its meaning, its grammar, and its dictionary entry.

Behind the scenes we made some big choices we're proud of. We rebranded from FlowUp to **SingoLing**, locked in a clean dark-mode design, and built an entire offline NLP pipeline so the app never has to "think" while you're listening — the lyrics just appear, instantly, the moment the music starts. We added a smart fallback that pulls definitions straight from Wiktionary when our primary dictionary is unavailable, so no word ever goes unexplained. And we set up an alignment worker powered by `faster-whisper` that can generate synced lyrics for songs that don't have them yet — meaning your favorite track is one click away from becoming a lesson.

By the end of the week SingoLing was live at **singoling.com**, served over HTTPS, with album-art-driven dynamic backgrounds, smooth song-to-song transitions, and a player that's already feeling like home. This is just the beginning, but it already feels like the future of language learning.

---

## Week 2 — Sing in Russian, Read in Your Language (May 10–16, 2026)

This week was all about meeting you where you are. We rolled out **Google and Apple Sign In** with brand-new login and signup flows, sorted our playlists into **Beginner, Intermediate, and Advanced** tiers so you can match the music to your level, and added full Russian playlists at every difficulty. If English is what you want to practice instead, we've got you — English playlists are live too, with native-quality definitions powered by our brand-new dictionary engines.

Here's the headline: SingoLing is now **truly multilingual on both sides of the screen**. We shipped Russian, German, Spanish, and Portuguese word translations this week, with coverage between **93% and 100%** on the songs we've tested. The grammar labels in the inspect panel now appear in your chosen interface language — English, Turkish, or Russian — so whether you're a Turkish speaker learning Russian or an English speaker learning Spanish, the whole experience speaks *to* you. And because nothing is ever perfect on the first try, we built a **"Report a problem"** button right into the lyrics: if a translation feels off, one tap sends it to us.

We also gave the player some serious polish — Chrome users will finally see their Cyrillic stress marks render correctly, song processing got a **26× speedup** so new music lands faster, and the Settings page now lets you choose your preferred music source, toggle pause-on-inspect, and message us directly from the Support tab. More languages, more songs, and more surprises are queued up for next week. Keep singing.
