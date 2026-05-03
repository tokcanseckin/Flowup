#!/usr/bin/env python3
"""
FlowUp – Russian Music Learning App
Data Generation Pipeline

Steps:
  1. Fetch time-synced lyrics (.lrc) from LRCLIB (free, no auth)
  2. Parse LRC timestamps → start_time_ms / end_time_ms
  3. Translate lines via DeepL API  (mocked when DEEPL_API_KEY is unset)
  4. Add contextual stress marks with ruaccent
  5. Tokenise + morphologically analyse with pymorphy3
  6. Write song_data.json consumed by the React frontend

Environment variables:
  DEEPL_API_KEY   – DeepL free-tier key  (optional – mock used otherwise)
  SPOTIFY_URI     – Override the default Spotify track URI

Usage:
  pip install -r requirements.txt
  DEEPL_API_KEY=your_key python generate_song_data.py
"""

from __future__ import annotations

import json
import os
import re
import sys
import time

import pymorphy3
import requests
from ruaccent import RUAccent


# ── Configuration ─────────────────────────────────────────────────────────────

DEEPL_API_KEY: str = os.environ.get("DEEPL_API_KEY", "")
DEEPL_URL: str = "https://api-free.deepl.com/v2/translate"  # paid: api.deepl.com

TRACK: dict = {
    "spotify_uri": os.environ.get(
        "SPOTIFY_URI", "spotify:track:4uLU6hMCjMI75M1A2tKUQC"
    ),
    "artist": "Кино",
    "title": "Группа Крови",
    "display_title": "Группа Крови — Кино",
}

# ── LRC helpers ───────────────────────────────────────────────────────────────

_LRC_RE = re.compile(r"\[(\d{2}):(\d{2})\.(\d{2,3})\](.*)")


def _ts_to_ms(mm: int, ss: int, frac: str) -> int:
    """Convert [mm:ss.xx] / [mm:ss.xxx] components to milliseconds."""
    return mm * 60_000 + ss * 1_000 + int(frac.ljust(3, "0"))


def parse_lrc(text: str) -> list[dict]:
    """Return list of {start_ms, end_ms, text} dicts from LRC content."""
    rows: list[dict] = []
    for raw in text.splitlines():
        m = _LRC_RE.match(raw.strip())
        if not m:
            continue
        mm, ss, frac, body = m.groups()
        body = body.strip()
        if body:
            rows.append({"start_ms": _ts_to_ms(int(mm), int(ss), frac), "text": body})

    for i in range(len(rows) - 1):
        rows[i]["end_ms"] = rows[i + 1]["start_ms"]
    if rows:
        rows[-1]["end_ms"] = rows[-1]["start_ms"] + 4_000

    return rows


# ── LRCLIB ────────────────────────────────────────────────────────────────────


def fetch_synced_lyrics(artist: str, title: str) -> str | None:
    """Fetch time-synced LRC from lrclib.net; returns raw LRC string or None."""
    print(f"  [LRCLIB] Searching for '{title}' by '{artist}' …")
    try:
        r = requests.get(
            "https://lrclib.net/api/search",
            params={"q": f"{artist} {title}"},
            timeout=12,
        )
        r.raise_for_status()
        for hit in r.json():
            if hit.get("syncedLyrics"):
                print(f"  [LRCLIB] Found synced lyrics (id={hit['id']}, "
                      f"title={hit.get('trackName', '?')})")
                return hit["syncedLyrics"]

        # Fallback: exact-match endpoint
        r2 = requests.get(
            "https://lrclib.net/api/get",
            params={"artist_name": artist, "track_name": title},
            timeout=12,
        )
        if r2.status_code == 200 and r2.json().get("syncedLyrics"):
            print("  [LRCLIB] Found via exact-match endpoint.")
            return r2.json()["syncedLyrics"]

        print("  [LRCLIB] No synced lyrics found for this track.")
    except requests.RequestException as exc:
        print(f"  [LRCLIB] Network error: {exc}")
    return None


# ── DeepL ─────────────────────────────────────────────────────────────────────

_MOCK_TAG = "[mock – set DEEPL_API_KEY] "


def translate_batch(texts: list[str]) -> list[str]:
    """Translate a list of Russian strings to English via DeepL."""
    if not DEEPL_API_KEY:
        print("  [DeepL] No API key set – returning mock translations.")
        return [_MOCK_TAG + t for t in texts]

    print(f"  [DeepL] Translating {len(texts)} lines …")
    try:
        r = requests.post(
            DEEPL_URL,
            headers={"Authorization": f"DeepL-Auth-Key {DEEPL_API_KEY}"},
            json={"text": texts, "source_lang": "RU", "target_lang": "EN-US"},
            timeout=30,
        )
        r.raise_for_status()
        return [t["text"] for t in r.json()["translations"]]
    except requests.RequestException as exc:
        print(f"  [DeepL] Error: {exc} – falling back to mocks.")
        return [_MOCK_TAG + t for t in texts]


# ── Grammar humaniser ─────────────────────────────────────────────────────────

_POS = {
    "NOUN": "Noun",      "ADJF": "Adjective",       "ADJS": "Adj (short)",
    "COMP": "Comparative","VERB": "Verb",             "INFN": "Verb (infinitive)",
    "PRTF": "Participle", "PRTS": "Participle (short)","GRND": "Gerund",
    "NUMR": "Numeral",    "ADVB": "Adverb",           "NPRO": "Pronoun",
    "PRED": "Predicative","PREP": "Preposition",      "CONJ": "Conjunction",
    "PRCL": "Particle",   "INTJ": "Interjection",
}
_CASE = {
    "nomn": "Nominative", "gent": "Genitive", "datv": "Dative",
    "accs": "Accusative", "ablt": "Ablative", "loct": "Locative",
    "voct": "Vocative",   "gen2": "Genitive 2",
}
_GEND = {"masc": "Masculine", "femn": "Feminine", "neut": "Neuter"}
_NUM  = {"sing": "Singular",  "plur": "Plural"}
_TENS = {"pres": "Present",   "past": "Past",   "futr": "Future"}
_PERS = {"1per": "1st Person","2per": "2nd Person","3per": "3rd Person"}


def humanize_grammar(parse) -> str:
    t = parse.tag
    parts: list[str] = []
    if t.POS:    parts.append(_POS.get(t.POS, t.POS))
    if t.tense:  parts.append(_TENS.get(t.tense, t.tense))
    if t.person: parts.append(_PERS.get(t.person, t.person))
    if t.number: parts.append(_NUM.get(t.number, t.number))
    if t.gender: parts.append(_GEND.get(t.gender, t.gender))
    if t.case:   parts.append(_CASE.get(t.case, t.case))
    return ", ".join(parts) or "Unknown"


# ── Line processor ────────────────────────────────────────────────────────────


def process_line(
    text: str,
    translation: str,
    start_ms: int,
    end_ms: int,
    morph: pymorphy3.MorphAnalyzer,
    accentizer: RUAccent,
) -> dict:
    """Return a fully-structured line dict matching the required JSON schema."""

    # Full-line contextual stress (ruaccent uses sentence context for omographs)
    stressed_line = accentizer.process_text(text)

    # Pair original and stressed tokens by whitespace split
    orig_tokens    = text.split()
    stressed_tokens = stressed_line.split()

    words: list[dict] = []
    key = 1

    for orig_tok, stress_tok in zip(orig_tokens, stressed_tokens):
        clean = re.sub(r"[^\w]", "", orig_tok, flags=re.UNICODE)
        if not clean:
            continue

        parses = morph.parse(clean)
        best   = parses[0] if parses else None

        if best:
            lemma          = best.normal_form
            lemma_stressed = accentizer.process_text(lemma)
            grammar        = humanize_grammar(best)
        else:
            lemma = lemma_stressed = clean
            grammar = "Unknown"

        words.append({
            "key": key,
            "inflected_stressed": stress_tok,
            "lemma_stressed": lemma_stressed,
            "grammar": grammar,
            # Mocked – replace with a real dictionary API call (e.g. Wiktionary)
            "dictionary_definition": f"[{lemma}]",
        })
        key += 1

    return {
        "start_time_ms": start_ms,
        "end_time_ms":   end_ms,
        "original_line": text,
        "stressed_line": stressed_line,
        "translation":   translation,
        "words":         words,
    }


# ── Main ──────────────────────────────────────────────────────────────────────


def main() -> None:
    sep = "─" * 60
    print(sep)
    print("  FlowUp — Data Generation Pipeline")
    print(sep)

    # ── 1. Lyrics
    print("\n[1/5] Fetching lyrics from LRCLIB …")
    lrc = fetch_synced_lyrics(TRACK["artist"], TRACK["title"])
    if lrc is None:
        print("ERROR: Could not retrieve synced lyrics. Aborting.")
        sys.exit(1)

    rows = parse_lrc(lrc)
    print(f"       Parsed {len(rows)} lyric lines.")

    # ── 2. Translations
    print("\n[2/5] Translating via DeepL …")
    translations = translate_batch([r["text"] for r in rows])

    # ── 3. NLP tools
    print("\n[3/5] Loading NLP models …")
    morph      = pymorphy3.MorphAnalyzer()
    accentizer = RUAccent()
    accentizer.load(omograph_model_size="turbo", use_dictionary=True)
    print("       pymorphy3 + ruaccent ready.")

    # ── 4. Per-line NLP
    print("\n[4/5] Processing lines …")
    lines: list[dict] = []
    for i, (row, trans) in enumerate(zip(rows, translations), 1):
        print(f"  {i:3}/{len(rows)}: {row['text'][:50]}")
        lines.append(
            process_line(
                row["text"], trans,
                row["start_ms"], row["end_ms"],
                morph, accentizer,
            )
        )
        time.sleep(0.02)  # gentle CPU pacing

    # ── 5. Write output
    print("\n[5/5] Writing song_data.json …")
    output = {
        "spotify_uri": TRACK["spotify_uri"],
        "title":       TRACK["display_title"],
        "lines":       lines,
    }
    out_path = os.path.join(os.path.dirname(__file__), "song_data.json")
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(output, fh, ensure_ascii=False, indent=2)

    print(f"\n✓  Done — {len(lines)} lines written to {out_path}")
    print(sep)


if __name__ == "__main__":
    main()
