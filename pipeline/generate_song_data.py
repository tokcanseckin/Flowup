#!/usr/bin/env python3
"""
FlowUp – Multilingual Data Generation Pipeline

Fetches time-synced lyrics from LRCLIB, translates via DeepL, runs
language-specific NLP (phonetic annotation + morphological analysis),
and writes a song_data.json file consumed by the React frontend.

Supported source languages (--lang):
  ru  Russian        (pymorphy3 + ruaccent)
  uk  Ukrainian      (pymorphy3)
  es  Spanish        (spaCy / generic)
  fr  French         (spaCy / generic)
  de  German         (spaCy / generic)
  it  Italian        (spaCy / generic)
  pt  Portuguese     (spaCy / generic)
  nl  Dutch          (spaCy / generic)
  pl  Polish         (spaCy / generic)
  sv  Swedish        (generic)
  tr  Turkish        (generic)
  ja  Japanese       (spaCy / generic)
  zh  Chinese        (spaCy / generic)
  ko  Korean         (spaCy / generic)
  ar  Arabic  [RTL]  (generic)
  he  Hebrew  [RTL]  (generic)

New language backends can be added by implementing nlp.NLPBackend and
registering an entry in LANGUAGES below.

Environment variables:
  DEEPL_API_KEY   – DeepL free-tier key (required for real translations)
  DEEPL_URL       – Override endpoint (default: api-free.deepl.com)
    ARGOS_AUTO_INSTALL – if "1", try to auto-download/install missing Argos model

Usage:
  pip install -r requirements.txt

  # Write JSON only:
  python generate_song_data.py \\
      --lang ru --artist "Кино" --title "Группа Крови" \\
      --spotify-uri "spotify:track:4uLU6hMCjMI75M1A2tKUQC"

  # Write JSON and push to the FlowUp backend DB:
  python generate_song_data.py \\
      --lang ru --artist "Кино" --title "Группа Крови" \\
      --spotify-uri "spotify:track:4uLU6hMCjMI75M1A2tKUQC" \\
      --api-url http://localhost:8000
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import requests

from nlp import GenericBackend, NLPBackend, PyMorphyBackend, SpaCyBackend


def _make_spacy_or_generic(lang_code: str) -> NLPBackend:
    """Return SpaCyBackend for the given language; fall back to GenericBackend."""
    try:
        return SpaCyBackend(lang_code)
    except Exception:
        return GenericBackend()

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

    # ── Latin-script European (spaCy when model installed, else Generic) ──────
    "es": LanguageConfig("Spanish",    "Latin",    "ltr", "ES",  lambda: _make_spacy_or_generic("es")),
    "fr": LanguageConfig("French",     "Latin",    "ltr", "FR",  lambda: _make_spacy_or_generic("fr")),
    "de": LanguageConfig("German",     "Latin",    "ltr", "DE",  lambda: _make_spacy_or_generic("de")),
    "it": LanguageConfig("Italian",    "Latin",    "ltr", "IT",  lambda: _make_spacy_or_generic("it")),
    "pt": LanguageConfig("Portuguese", "Latin",    "ltr", "PT",  lambda: _make_spacy_or_generic("pt")),
    "nl": LanguageConfig("Dutch",      "Latin",    "ltr", "NL",  lambda: _make_spacy_or_generic("nl")),
    "pl": LanguageConfig("Polish",     "Latin",    "ltr", "PL",  lambda: _make_spacy_or_generic("pl")),
    "sv": LanguageConfig("Swedish",    "Latin",    "ltr", "SV",  GenericBackend),
    "tr": LanguageConfig("Turkish",    "Latin",    "ltr", "TR",  GenericBackend),

    # ── East Asian (spaCy when model installed, else Generic) ─────────────────
    "ja": LanguageConfig("Japanese",   "CJK",      "ltr", "JA",  lambda: _make_spacy_or_generic("ja")),
    "zh": LanguageConfig("Chinese",    "CJK",      "ltr", "ZH",  lambda: _make_spacy_or_generic("zh")),
    "ko": LanguageConfig("Korean",     "Hangul",   "ltr", "KO",  lambda: _make_spacy_or_generic("ko")),

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
    p.add_argument("--api-url",     dest="api_url", default="",
                   help="FlowUp backend URL (e.g. http://localhost:8000). "
                        "When set, the processed song is also pushed to the backend database.")
    p.add_argument("--lrc-file",    dest="lrc_file", default="",
                   help="Path to a local .lrc file to use instead of fetching from LRCLIB.")
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


def load_local_lrc(path: str) -> str | None:
    """Read a .lrc file from disk."""
    try:
        with open(path, encoding="utf-8") as f:
            content = f.read()
        print(f"  [LRC] Loaded from '{path}' ({len(content)} chars).")
        return content
    except OSError as exc:
        print(f"  [LRC] Could not read '{path}': {exc}")
        return None

# ── DeepL ─────────────────────────────────────────────────────────────────────

_DEEPL_API_KEY = os.environ.get("DEEPL_API_KEY", "")
_DEEPL_URL     = os.environ.get("DEEPL_URL", "https://api-free.deepl.com/v2/translate")
_MOCK_TAG      = "[mock – set DEEPL_API_KEY] "
_ARGOS_AUTO_INSTALL = os.environ.get("ARGOS_AUTO_INSTALL", "0") == "1"


def _normalize_lang_for_argos(code: str) -> str:
    """Convert language tags (e.g. EN-US) into Argos-compatible code (en)."""
    return code.split("-")[0].lower()


def _translate_batch_argos(texts: list[str], source_lang: str, target_lang: str) -> list[str] | None:
    """
    Offline translation using Argos Translate.

    Returns translated lines when a suitable Argos model is available,
    otherwise returns None so the caller can fall back.
    """
    src = _normalize_lang_for_argos(source_lang)
    tgt = _normalize_lang_for_argos(target_lang)

    try:
        import argostranslate.package  # type: ignore[import-untyped]
        import argostranslate.translate  # type: ignore[import-untyped]
    except Exception:
        print("  [Argos] Package not installed; skipping offline fallback.")
        return None

    available = argostranslate.translate.get_installed_languages()
    src_lang = next((l for l in available if l.code == src), None)
    tgt_lang = next((l for l in available if l.code == tgt), None)

    if src_lang and tgt_lang:
        translation = src_lang.get_translation(tgt_lang)
        if translation:
            print(f"  [Argos] Translating {len(texts)} lines ({src} -> {tgt}) using local model.")
            return [translation.translate(t) for t in texts]

    if not _ARGOS_AUTO_INSTALL:
        print(
            "  [Argos] No installed model for "
            f"{src}->{tgt}. Set ARGOS_AUTO_INSTALL=1 to auto-download, "
            "or install model manually."
        )
        return None

    # Optional: auto-download package index/model when requested.
    try:
        print(f"  [Argos] Attempting model auto-install for {src}->{tgt} ...")
        argostranslate.package.update_package_index()
        packages = argostranslate.package.get_available_packages()
        pkg = next((p for p in packages if p.from_code == src and p.to_code == tgt), None)
        if not pkg:
            print(f"  [Argos] No downloadable package found for {src}->{tgt}.")
            return None
        path = pkg.download()
        argostranslate.package.install_from_path(path)

        # Reload installed languages after installation.
        available = argostranslate.translate.get_installed_languages()
        src_lang = next((l for l in available if l.code == src), None)
        tgt_lang = next((l for l in available if l.code == tgt), None)
        if not src_lang or not tgt_lang:
            return None
        translation = src_lang.get_translation(tgt_lang)
        if not translation:
            return None

        print(f"  [Argos] Auto-install successful; translating {len(texts)} lines.")
        return [translation.translate(t) for t in texts]
    except Exception as exc:
        print(f"  [Argos] Auto-install failed: {exc}")
        return None


def translate_batch(texts: list[str], source_lang: str, target_lang: str) -> list[str]:
    if not _DEEPL_API_KEY:
        print("  [DeepL] No API key – trying Argos offline fallback.")
        argos_result = _translate_batch_argos(texts, source_lang, target_lang)
        if argos_result is not None:
            return argos_result
        print("  [DeepL] Argos unavailable – returning mock translations.")
        return [_MOCK_TAG + t for t in texts]

    print(f"  [DeepL] Translating {len(texts)} lines ({source_lang} → {target_lang}) …")
    max_retries = 3
    for attempt in range(1, max_retries + 1):
        try:
            r = requests.post(
                _DEEPL_URL,
                headers={"Authorization": f"DeepL-Auth-Key {_DEEPL_API_KEY}"},
                json={"text": texts, "source_lang": source_lang, "target_lang": target_lang},
                timeout=30,
            )
            if r.status_code == 429:
                wait = 2 ** attempt
                print(f"  [DeepL] Rate limited — waiting {wait}s (attempt {attempt}/{max_retries}) …")
                time.sleep(wait)
                continue
            r.raise_for_status()
            return [t["text"] for t in r.json()["translations"]]
        except requests.RequestException as exc:
            if attempt == max_retries:
                print(f"  [DeepL] Error after {max_retries} attempts: {exc} – trying Argos offline fallback.")
                break
            print(f"  [DeepL] Attempt {attempt} failed: {exc} — retrying …")
            time.sleep(2 ** attempt)

    argos_result = _translate_batch_argos(texts, source_lang, target_lang)
    if argos_result is not None:
        return argos_result

    print("  [DeepL] Argos unavailable – returning mock translations.")
    return [_MOCK_TAG + t for t in texts]

# ── Line processor ────────────────────────────────────────────────────────────

# ── OpenRussian lookup ────────────────────────────────────────────────────────

_or_lookup_fn = None  # set to a callable once loaded


def _load_openrussian() -> None:
    """Attempt to load the OpenRussian index from the backend cache."""
    global _or_lookup_fn
    # Try to import the backend's openrussian module (backend must be in sys.path)
    backend_dir = str((Path(__file__).parent.parent / "backend").resolve())
    if backend_dir not in sys.path:
        sys.path.insert(0, backend_dir)
    try:
        from openrussian import ensure_loaded, lookup  # type: ignore[import]
        ensure_loaded()
        _or_lookup_fn = lookup
        print("  [OpenRussian] Dictionary loaded.")
    except Exception as exc:
        print(f"  [OpenRussian] Could not load dictionary: {exc}")
        _or_lookup_fn = None


def _resolve_definition(lemma: str, lang_code: str) -> str:
    """Return a definition string for the lemma (OpenRussian for 'ru', else stub)."""
    clean_lemma = re.sub(r"[^\w]", "", lemma, flags=re.UNICODE)
    if lang_code == "ru" and _or_lookup_fn is not None:
        definition = _or_lookup_fn(clean_lemma)
        if definition:
            return definition
    return f"[{clean_lemma}]"  # stub — replaced by backend on the fly for Russian


# ── Backend push ──────────────────────────────────────────────────────────────

def push_to_backend(api_url: str, payload: dict) -> None:
    """POST the processed song JSON to the FlowUp backend API."""
    url = api_url.rstrip("/") + "/api/songs"
    print(f"\n[6/5] Pushing to backend: {url} …")
    try:
        r = requests.post(url, json=payload, timeout=30)
        if not r.ok:
            print(f"  [Backend] Error {r.status_code}: {r.text[:200]}")
        else:
            data = r.json()
            print(f"  [Backend] Song stored (id={data.get('id')}).")
    except requests.RequestException as exc:
        print(f"  [Backend] Network error: {exc}")


# ── Line processor ────────────────────────────────────────────────────────────


def process_line(
    text: str,
    translation: str,
    start_ms: int,
    end_ms: int,
    backend: NLPBackend,
    lang_code: str = "",
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
            "dictionary_definition": _resolve_definition(analysis.lemma, lang_code),
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
    if args.lrc_file:
        print("\n[1/5] Loading lyrics from local file …")
        lrc = load_local_lrc(args.lrc_file)
    else:
        print("\n[1/5] Fetching lyrics from LRCLIB …")
        lrc = fetch_synced_lyrics(args.artist, args.title)
    if lrc is None:
        print("ERROR: Could not retrieve synced lyrics. Aborting.")
        print("TIP:   Pass --lrc-file <path> to use a local .lrc file as a fallback.")
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

    # ── 3b. OpenRussian (Russian only)
    if args.lang == "ru":
        _load_openrussian()

    # ── 4. Per-line processing
    print("\n[4/5] Processing lines …")
    lines: list[dict] = []
    for i, (row, trans) in enumerate(zip(rows, translations), 1):
        print(f"  {i:3}/{len(rows)}: {row['text'][:55]}")
        lines.append(process_line(
            row["text"], trans,
            row["start_ms"], row["end_ms"],
            backend,
            lang_code=args.lang,
        ))
        time.sleep(0.02)

    # ── 5. Write output JSON
    print(f"\n[5/5] Writing {args.output} …")
    output = {
        "spotify_uri": args.spotify_uri,
        "title":       args.display_title or args.title,
        "artist":      args.artist,
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
    print(f"       Written to {out_path}")

    # ── 6. Push to backend (optional)
    if args.api_url:
        push_to_backend(args.api_url, output)

    print(f"\n✓  Done — {len(lines)} lines.")
    print(sep)


if __name__ == "__main__":
    main()
