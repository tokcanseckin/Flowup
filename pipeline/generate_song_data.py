#!/usr/bin/env python3
"""
FlowUp – Multilingual Data Generation Pipeline

Fetches time-synced lyrics from LRCLIB, translates via DeepL, runs
language-specific NLP (phonetic annotation + morphological analysis),
and writes a song_data.json file consumed by the React frontend.

Supported source languages (--lang):
  ru  Russian        (pymorphy3 + ruaccent)
  uk  Ukrainian      (pymorphy3)
  es  Spanish        (generic)
  fr  French         (generic)
  de  German         (generic)
  it  Italian        (generic)
  pt  Portuguese     (generic)
  nl  Dutch          (generic)
  pl  Polish         (generic)
  sv  Swedish        (generic)
  tr  Turkish        (generic)
  ja  Japanese       (generic)
  zh  Chinese        (generic)
  ko  Korean         (generic)
  ar  Arabic  [RTL]  (generic)
  he  Hebrew  [RTL]  (generic)

New language backends can be added by implementing nlp.NLPBackend and
registering an entry in LANGUAGES below.

Environment variables:
  DEEPL_API_KEY   – DeepL free-tier key (required for real translations)
  DEEPL_URL       – Override endpoint (default: api-free.deepl.com)

Usage:
  pip install -r requirements.txt
  python generate_song_data.py \\
      --lang ru \\
      --artist "Кино" \\
      --title  "Группа Крови" \\
      --spotify-uri "spotify:track:4uLU6hMCjMI75M1A2tKUQC"
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from dataclasses import dataclass
from typing import Callable

import requests

from nlp import GenericBackend, NLPBackend, PyMorphyBackend

# ── Language registry ─────────────────────────────────────────────────────────

@dataclass
class LanguageConfig:
    name:         str                    # display name
    script:       str                    # writing system family
    direction:    str                    # 'ltr' | 'rtl'
    deepl_code:   str                    # DeepL source_lang code
    make_backend: Callable[[], NLPBackend]

LANGUAGES: dict[str, LanguageConfig] = {
    # ── Slavic (pymorphy3) ────────────────────────────────────────────────────
    "ru": LanguageConfig("Russian",    "Cyrillic", "ltr", "RU",
                         lambda: PyMorphyBackend(morph_lang="ru", use_accent=True)),
    "uk": LanguageConfig("Ukrainian",  "Cyrillic", "ltr", "UK",
                         lambda: PyMorphyBackend(morph_lang="uk", use_accent=False)),

    # ── Latin-script European ─────────────────────────────────────────────────
    "es": LanguageConfig("Spanish",    "Latin",    "ltr", "ES",  GenericBackend),
    "fr": LanguageConfig("French",     "Latin",    "ltr", "FR",  GenericBackend),
    "de": LanguageConfig("German",     "Latin",    "ltr", "DE",  GenericBackend),
    "it": LanguageConfig("Italian",    "Latin",    "ltr", "IT",  GenericBackend),
    "pt": LanguageConfig("Portuguese", "Latin",    "ltr", "PT",  GenericBackend),
    "nl": LanguageConfig("Dutch",      "Latin",    "ltr", "NL",  GenericBackend),
    "pl": LanguageConfig("Polish",     "Latin",    "ltr", "PL",  GenericBackend),
    "sv": LanguageConfig("Swedish",    "Latin",    "ltr", "SV",  GenericBackend),
    "tr": LanguageConfig("Turkish",    "Latin",    "ltr", "TR",  GenericBackend),

    # ── East Asian ────────────────────────────────────────────────────────────
    "ja": LanguageConfig("Japanese",   "CJK",      "ltr", "JA",  GenericBackend),
    "zh": LanguageConfig("Chinese",    "CJK",      "ltr", "ZH",  GenericBackend),
    "ko": LanguageConfig("Korean",     "Hangul",   "ltr", "KO",  GenericBackend),

    # ── Right-to-left ─────────────────────────────────────────────────────────
    "ar": LanguageConfig("Arabic",     "Arabic",   "rtl", "AR",  GenericBackend),
    "he": LanguageConfig("Hebrew",     "Hebrew",   "rtl", "HE",  GenericBackend),
}

# ── CLI ───────────────────────────────────────────────────────────────────────

def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="FlowUp multilingual data pipeline",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--lang",        required=True, choices=LANGUAGES,
                   metavar="LANG",  help=f"Source language code. One of: {', '.join(LANGUAGES)}")
    p.add_argument("--artist",      required=True, help="Artist name (used for LRCLIB search)")
    p.add_argument("--title",       required=True, help="Track title (used for LRCLIB search)")
    p.add_argument("--spotify-uri", required=True, dest="spotify_uri",
                   help="Spotify track URI, e.g. spotify:track:4uLU6hMCjMI75M1A2tKUQC")
    p.add_argument("--display-title", dest="display_title",
                   help="Song title shown in the UI (defaults to --title)")
    p.add_argument("--target-lang", dest="target_lang", default="EN-US",
                   help="DeepL translation target language (default: EN-US)")
    p.add_argument("--offset-ms",   dest="offset_ms", type=int, default=0,
                   help="Add a fixed ms offset to all timestamps (positive = shift later)")
    p.add_argument("--output",      default="song_data.json",
                   help="Output file path (default: song_data.json)")
    return p

# ── LRC helpers ───────────────────────────────────────────────────────────────

_LRC_RE = re.compile(r"\[(\d{2}):(\d{2})\.(\d{2,3})\](.*)")


def _ts_to_ms(mm: int, ss: int, frac: str) -> int:
    return mm * 60_000 + ss * 1_000 + int(frac.ljust(3, "0"))


def parse_lrc(text: str, offset_ms: int = 0) -> list[dict]:
    rows: list[dict] = []
    for raw in text.splitlines():
        m = _LRC_RE.match(raw.strip())
        if not m:
            continue
        mm, ss, frac, body = m.groups()
        body = body.strip()
        if body:
            rows.append({
                "start_ms": _ts_to_ms(int(mm), int(ss), frac) + offset_ms,
                "text": body,
            })
    for i in range(len(rows) - 1):
        rows[i]["end_ms"] = rows[i + 1]["start_ms"]
    if rows:
        rows[-1]["end_ms"] = rows[-1]["start_ms"] + 4_000
    return rows

# ── LRCLIB ────────────────────────────────────────────────────────────────────

def fetch_synced_lyrics(artist: str, title: str) -> str | None:
    print(f"  [LRCLIB] Searching '{title}' by '{artist}' …")
    try:
        r = requests.get(
            "https://lrclib.net/api/search",
            params={"q": f"{artist} {title}"},
            timeout=12,
        )
        r.raise_for_status()
        for hit in r.json():
            if hit.get("syncedLyrics"):
                print(f"  [LRCLIB] Found (id={hit['id']}, title={hit.get('trackName', '?')})")
                return hit["syncedLyrics"]

        r2 = requests.get(
            "https://lrclib.net/api/get",
            params={"artist_name": artist, "track_name": title},
            timeout=12,
        )
        if r2.status_code == 200 and r2.json().get("syncedLyrics"):
            print("  [LRCLIB] Found via exact-match endpoint.")
            return r2.json()["syncedLyrics"]

        print("  [LRCLIB] No synced lyrics found.")
    except requests.RequestException as exc:
        print(f"  [LRCLIB] Network error: {exc}")
    return None

# ── DeepL ─────────────────────────────────────────────────────────────────────

_DEEPL_API_KEY = os.environ.get("DEEPL_API_KEY", "")
_DEEPL_URL     = os.environ.get("DEEPL_URL", "https://api-free.deepl.com/v2/translate")
_MOCK_TAG      = "[mock – set DEEPL_API_KEY] "


def translate_batch(texts: list[str], source_lang: str, target_lang: str) -> list[str]:
    if not _DEEPL_API_KEY:
        print("  [DeepL] No API key – returning mock translations.")
        return [_MOCK_TAG + t for t in texts]

    print(f"  [DeepL] Translating {len(texts)} lines ({source_lang} → {target_lang}) …")
    try:
        r = requests.post(
            _DEEPL_URL,
            headers={"Authorization": f"DeepL-Auth-Key {_DEEPL_API_KEY}"},
            json={"text": texts, "source_lang": source_lang, "target_lang": target_lang},
            timeout=30,
        )
        r.raise_for_status()
        return [t["text"] for t in r.json()["translations"]]
    except requests.RequestException as exc:
        print(f"  [DeepL] Error: {exc} – falling back to mocks.")
        return [_MOCK_TAG + t for t in texts]

# ── Line processor ────────────────────────────────────────────────────────────

def process_line(
    text: str,
    translation: str,
    start_ms: int,
    end_ms: int,
    backend: NLPBackend,
) -> dict:
    phonetic_line = backend.annotate_line(text)  # None for languages with no annotation

    orig_tokens  = text.split()
    annot_tokens = phonetic_line.split() if phonetic_line else orig_tokens

    words: list[dict] = []
    key = 1

    for raw_tok, annot_tok in zip(orig_tokens, annot_tokens):
        clean = re.sub(r"[^\w]", "", raw_tok, flags=re.UNICODE)
        if not clean:
            continue

        analysis = backend.analyze_token(raw_tok, annot_tok)

        words.append({
            "key":                  key,
            "display_form":         analysis.display_form,
            "lemma":                analysis.lemma,
            "grammar":              analysis.grammar,
            "dictionary_definition": f"[{re.sub(r'[^\w]', '', analysis.lemma, flags=re.UNICODE)}]",
        })
        key += 1

    return {
        "start_time_ms": start_ms,
        "end_time_ms":   end_ms,
        "original_line": text,
        "phonetic_line": phonetic_line,   # null when backend returns None
        "translation":   translation,
        "words":         words,
    }

# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    args   = build_arg_parser().parse_args()
    lang   = LANGUAGES[args.lang]
    sep    = "─" * 60

    print(sep)
    print(f"  FlowUp — Data Pipeline  [{lang.name}]")
    print(sep)

    # ── 1. Lyrics
    print("\n[1/5] Fetching lyrics from LRCLIB …")
    lrc = fetch_synced_lyrics(args.artist, args.title)
    if lrc is None:
        print("ERROR: Could not retrieve synced lyrics. Aborting.")
        sys.exit(1)
    rows = parse_lrc(lrc, args.offset_ms)
    print(f"       Parsed {len(rows)} lyric lines.")

    # ── 2. Translations
    print("\n[2/5] Translating via DeepL …")
    translations = translate_batch(
        [r["text"] for r in rows],
        source_lang=lang.deepl_code,
        target_lang=args.target_lang,
    )

    # ── 3. NLP backend
    print(f"\n[3/5] Loading {lang.name} NLP backend …")
    backend = lang.make_backend()
    backend.load()

    # ── 4. Per-line processing
    print("\n[4/5] Processing lines …")
    lines: list[dict] = []
    for i, (row, trans) in enumerate(zip(rows, translations), 1):
        print(f"  {i:3}/{len(rows)}: {row['text'][:55]}")
        lines.append(process_line(
            row["text"], trans,
            row["start_ms"], row["end_ms"],
            backend,
        ))
        time.sleep(0.02)

    # ── 5. Write output
    print(f"\n[5/5] Writing {args.output} …")
    output = {
        "spotify_uri": args.spotify_uri,
        "title":       args.display_title or args.title,
        "language": {
            "code":      args.lang,
            "name":      lang.name,
            "script":    lang.script,
            "direction": lang.direction,
        },
        "lines": lines,
    }
    out_path = os.path.join(os.path.dirname(__file__), args.output)
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(output, fh, ensure_ascii=False, indent=2)

    print(f"\n✓  Done — {len(lines)} lines → {out_path}")
    print(sep)


if __name__ == "__main__":
    main()
