#!/usr/bin/env python3
"""
FlowUp – Multilingual Data Generation Pipeline

Fetches time-synced lyrics from LRCLIB, falls back to stable-ts forced
alignment when only plain lyrics are available, translates via DeepL, runs
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
  DEEPL_API_KEY      – DeepL free-tier key (required for real translations)
  DEEPL_URL          – Override endpoint (default: api-free.deepl.com)
  ARGOS_AUTO_INSTALL – if "1", try to auto-download/install missing Argos model
  YOUTUBE_COOKIES_FILE – Path to Netscape-format cookies file for yt-dlp (bypasses bot detection)
  STABLE_TS_MODEL      – stable-ts Whisper model for forced alignment (default: large-v3-turbo)

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
import subprocess
import sys
import tempfile
import time
import unicodedata
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
    p.add_argument("--youtube-url", dest="youtube_url", default="",
                   help="YouTube URL used for stable-ts forced alignment when synced lyrics are unavailable.")
    p.add_argument("--stable-ts-model", dest="stable_ts_model",
                   default=os.environ.get("STABLE_TS_MODEL", "large-v3-turbo"),
                   help="stable-ts Whisper model name (default: large-v3-turbo)")
    p.add_argument("--replace-id",  dest="replace_id", type=int, default=None,
                   help="Delete this song ID from the backend before pushing the re-processed version.")
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


def _format_lrc_timestamp(ms: int) -> str:
    total_ms = max(0, ms)
    minutes = total_ms // 60_000
    seconds = (total_ms % 60_000) // 1_000
    centiseconds = (total_ms % 1_000) // 10
    return f"{minutes:02}:{seconds:02}.{centiseconds:02}"


def rows_to_lrc(rows: list[dict]) -> str:
    return "\n".join(f"[{_format_lrc_timestamp(row['start_ms'])}]{row['text']}" for row in rows)


def _normalize_plain_lyrics(text: str) -> list[str]:
    """Strip blank lines and section markers like [Chorus], [Verse 1], etc."""
    lines: list[str] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if re.match(r"^\[[^\]]+\]$", line):
            continue
        lines.append(line)
    return lines


# ── stable-ts forced-alignment helpers ───────────────────────────────────────

_STTS_METADATA_PAT = re.compile(r'^\(.*\)$')
_STTS_STOPWORDS = {"я", "и", "в", "на", "не", "но", "он", "её", "мне",
                   "мой", "моя", "его", "мы", "вы", "ты", "да", "нет", "вот"}


def _stts_filter_metadata(lines: list[str]) -> list[str]:
    """Remove parenthetical-only lines (e.g. composer credits like '(Авт. текста)')."""
    filtered = [l for l in lines if not _STTS_METADATA_PAT.match(l)]
    if len(filtered) < len(lines):
        print(f"  [stable-ts] Removed {len(lines) - len(filtered)} metadata line(s)")
    return filtered


def _stts_norm(s: str) -> str:
    s = unicodedata.normalize("NFC", s.lower())
    return re.sub(r"[^\w]", "", s, flags=re.UNICODE)


def _stts_anchor_for(lw: list[str]) -> tuple[int, list[str]]:
    for i, w in enumerate(lw):
        nw = _stts_norm(w)
        if nw not in _STTS_STOPWORDS and len(nw) >= 3:
            anchor = [nw]
            if i + 1 < len(lw):
                anchor.append(_stts_norm(lw[i + 1]))
            return i, anchor
    return 0, [_stts_norm(lw[0])]


def _stts_collect_words(result) -> list[tuple[float, float, str]]:
    words = []
    for seg in result.segments:
        for w in (seg.words or []):
            t = w.word.strip()
            if t:
                words.append((w.start, w.end, t))
    return words


def _stts_match_lines(lyrics: list[str], words: list[tuple]) -> list:
    out = []
    wi = 0
    for line in lyrics:
        lw = line.split()
        if not lw:
            out.append(None)
            continue
        offset, anchors = _stts_anchor_for(lw)
        found = None
        for i in range(wi, len(words)):
            if _stts_norm(words[i][2]) == anchors[0]:
                if len(anchors) > 1 and i + 1 < len(words):
                    if _stts_norm(words[i + 1][2]) != anchors[1]:
                        continue
                si = max(0, i - offset)
                ei = min(si + len(lw) - 1, len(words) - 1)
                found = (si, ei, words[si][0], words[ei][1])
                wi = i + 1
                break
        out.append(found)
    return out


def _stts_is_stacked(words: list[tuple], si: int, ei: int) -> bool:
    from collections import Counter
    if ei <= si:
        return False
    times = [round(words[j][0], 2) for j in range(si, min(ei + 1, len(words)))]
    return max(Counter(times).values(), default=0) >= 3


def _stts_audio_duration(audio_path: Path) -> float:
    r = subprocess.run(
        ["ffprobe", "-v", "quiet", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(audio_path)],
        capture_output=True, text=True,
    )
    try:
        return float(r.stdout.strip())
    except ValueError:
        return 0.0


def _stts_isolate_vocals(audio_path: Path, work_dir: Path) -> Path:
    print("  [stable-ts] Isolating vocals with Demucs …")
    t0 = time.perf_counter()
    demucs_out = work_dir / "demucs_out"
    r = subprocess.run(
        [sys.executable, "-m", "demucs",
         "--two-stems", "vocals", "-n", "htdemucs",
         "-o", str(demucs_out), str(audio_path)],
        capture_output=True, text=True, timeout=600,
    )
    if r.returncode != 0:
        print(f"  [stable-ts] Demucs failed — using original audio")
        return audio_path
    candidates = list(demucs_out.rglob("vocals.wav"))
    if not candidates:
        print("  [stable-ts] vocals.wav not found — using original audio")
        return audio_path
    print(f"  [stable-ts] Demucs done in {time.perf_counter() - t0:.1f}s")
    return candidates[0]


def _stts_ngap_loop(
    model,
    audio_path: Path,
    lyrics: list[str],
    language: str,
    work_dir: Path,
    offset_abs: float = 0.0,
    gap_threshold: float = 15.0,
    lookback: float = 25.0,
) -> tuple[list[tuple], float]:
    """N-gap forced alignment loop with density guard."""
    all_words: list[tuple] = []
    committed = 0
    sub_start = 0.0
    consecutive_no_progress = 0
    align_time = 0.0
    MAX_PASSES = 10
    MAX_LINE_DENSITY = 0.5   # lines/s — above this, alignment reliably fails
    audio_total = _stts_audio_duration(audio_path)

    for pass_num in range(1, MAX_PASSES + 1):
        remaining = lyrics[committed:]
        if not remaining:
            break

        audio_remaining = max(audio_total - sub_start, 1.0)
        density = len(remaining) / audio_remaining
        if density > MAX_LINE_DENSITY:
            print(f"  [stable-ts] pass {pass_num}: ⚠ density too high "
                  f"({len(remaining)} lines / {audio_remaining:.0f}s = {density:.2f}/s) — stopping")
            break

        if sub_start > 0:
            sub_crop = work_dir / f".ngap_{int(offset_abs + sub_start)}.wav"
            if not sub_crop.exists():
                subprocess.run(
                    ["ffmpeg", "-y", "-i", str(audio_path), "-ss", str(sub_start), str(sub_crop)],
                    capture_output=True, check=True,
                )
            align_src = sub_crop
        else:
            align_src = audio_path

        t0 = time.perf_counter()
        result = model.align(str(align_src), "\n".join(remaining), language=language)
        align_time += time.perf_counter() - t0

        words_rel = _stts_collect_words(result)
        abs_off = offset_abs + sub_start
        words_abs = [(s + abs_off, e + abs_off, w) for s, e, w in words_rel]
        assignments = _stts_match_lines(remaining, words_rel)

        first_bad = None
        for i, a in enumerate(assignments):
            if a and _stts_is_stacked(words_rel, a[0], a[1]):
                first_bad = i
                break

        if first_bad is None:
            first_wi = assignments[0][0] if assignments[0] else 0
            all_words += words_abs[first_wi:]
            committed = len(lyrics)
            break

        bad_time_rel = assignments[first_bad][2]
        major_gap = None
        for j in range(1, len(words_rel)):
            gap = words_rel[j][0] - words_rel[j - 1][1]
            if gap >= gap_threshold and words_rel[j - 1][1] >= bad_time_rel - lookback:
                major_gap = (words_rel[j - 1][1], words_rel[j][0])
                break

        if major_gap is None:
            first_wi = assignments[0][0] if assignments[0] else 0
            all_words += words_abs[first_wi:]
            committed = len(lyrics)
            break

        gap_start_rel, gap_end_rel = major_gap
        good_count = 0
        last_good_wi_rel = -1
        for i, a in enumerate(assignments):
            if i >= first_bad:
                break
            if a and a[3] <= gap_start_rel:
                good_count = i + 1
                last_good_wi_rel = a[1]

        new_sub_start = sub_start + max(0.0, gap_end_rel - lookback)

        if good_count > 0:
            first_wi = assignments[0][0] if assignments[0] else 0
            all_words += words_abs[first_wi:last_good_wi_rel + 1]
            committed += good_count
            consecutive_no_progress = 0
            print(f"  [stable-ts] pass {pass_num}: gap "
                  f"{abs_off + gap_start_rel:.1f}→{abs_off + gap_end_rel:.1f}s — "
                  f"committed {committed}/{len(lyrics)} lines")
        else:
            consecutive_no_progress += 1
            if consecutive_no_progress >= 2:
                first_wi = assignments[0][0] if assignments[0] else 0
                all_words += words_abs[first_wi:]
                committed = len(lyrics)
                break

        sub_start = new_sub_start

    return all_words, align_time


def align_with_stable_ts(
    plain_lyrics: str,
    youtube_url: str,
    lang_code: str,
    model_name: str = "large-v3-turbo",
) -> str | None:
    """Download audio via yt-dlp, isolate vocals with Demucs, then force-align
    lyrics using stable-ts (Whisper encoder). Returns LRC string or None.
    """
    if not youtube_url:
        print("  [stable-ts] Skipping: no YouTube URL provided.")
        return None

    lyric_lines = _normalize_plain_lyrics(plain_lyrics)
    lyric_lines = _stts_filter_metadata(lyric_lines)
    if not lyric_lines:
        print("  [stable-ts] Skipping: no usable lyric lines.")
        return None

    try:
        import stable_whisper
    except ImportError:
        print("  [stable-ts] stable-ts not installed — pip install stable-ts")
        return None

    with tempfile.TemporaryDirectory(prefix="flowup-stablts-") as tmp_dir:
        tmp = Path(tmp_dir)
        audio_path = tmp / "audio.mp3"

        # ── Download audio
        print("  [stable-ts] Downloading audio via yt-dlp …")
        yt_cmd = [
            sys.executable, "-m", "yt_dlp",
            "-f", "bestaudio[acodec!=none]/best[acodec!=none]",
            "-x", "--audio-format", "mp3",
            "-o", str(tmp / "audio.%(ext)s"),
            "--no-playlist",
        ]
        cookies_file = os.environ.get("YOUTUBE_COOKIES_FILE", "")
        if cookies_file and Path(cookies_file).exists():
            yt_cmd += ["--cookies", cookies_file]
            print(f"  [stable-ts] Using cookies from {cookies_file}")
        yt_cmd.append(youtube_url)
        dl = subprocess.run(yt_cmd, capture_output=True, text=True, timeout=180, check=False)
        if dl.returncode != 0 or not audio_path.exists():
            print(f"  [stable-ts] Download failed: {(dl.stderr or dl.stdout).strip()[:400]}")
            return None

        # ── Isolate vocals with Demucs
        vocals_path = _stts_isolate_vocals(audio_path, tmp)

        # ── Load model
        print(f"  [stable-ts] Loading model '{model_name}' …")
        t0 = time.perf_counter()
        model = stable_whisper.load_model(model_name)
        print(f"  [stable-ts] Model loaded in {time.perf_counter() - t0:.1f}s")

        # ── Align
        print(f"  [stable-ts] Aligning {len(lyric_lines)} lines …")
        all_words, align_time = _stts_ngap_loop(
            model, vocals_path, lyric_lines, lang_code,
            work_dir=tmp, offset_abs=0.0,
            gap_threshold=15.0, lookback=25.0,
        )
        print(f"  [stable-ts] Alignment done in {align_time:.1f}s")

        # ── Build LRC rows: dedup consecutive same-timestamp stacked lines
        assignments = _stts_match_lines(lyric_lines, all_words)
        rows: list[dict] = []
        last_stacked_start: float | None = None
        for a, line in zip(assignments, lyric_lines):
            if not a:
                print(f"  [stable-ts] ⚠ unaligned (skipped): {line[:60]}")
                continue
            stacked = _stts_is_stacked(all_words, a[0], a[1])
            if stacked:
                if last_stacked_start is not None and abs(a[2] - last_stacked_start) < 0.5:
                    print(f"  [stable-ts] ⚠ duplicate skipped: {line[:60]}")
                    continue
                last_stacked_start = a[2]
            else:
                last_stacked_start = None
            rows.append({"start_ms": int(a[2] * 1000), "end_ms": int(a[3] * 1000), "text": line})

        if not rows:
            print("  [stable-ts] No lines aligned.")
            return None

        for i in range(len(rows) - 1):
            rows[i]["end_ms"] = rows[i + 1]["start_ms"]
        rows[-1]["end_ms"] = rows[-1]["start_ms"] + 4_000

        print(f"  [stable-ts] Aligned {len(rows)}/{len(lyric_lines)} lines.")
        return rows_to_lrc(rows)


# ── LRCLIB ────────────────────────────────────────────────────────────────────

def _fetch_lrclib_candidate(artist: str, title: str) -> dict | None:
    try:
        r = requests.get(
            "https://lrclib.net/api/search",
            params={"q": f"{artist} {title}"},
            timeout=12,
        )
        r.raise_for_status()
        for hit in r.json():
            if hit.get("syncedLyrics") or hit.get("plainLyrics"):
                return hit

        r2 = requests.get(
            "https://lrclib.net/api/get",
            params={"artist_name": artist, "track_name": title},
            timeout=12,
        )
        if r2.status_code == 200:
            body = r2.json()
            if isinstance(body, dict) and (body.get("syncedLyrics") or body.get("plainLyrics")):
                return body
    except requests.RequestException as exc:
        print(f"  [LRCLIB] Network error: {exc}")
    return None

def fetch_synced_lyrics(artist: str, title: str) -> str | None:
    print(f"  [LRCLIB] Searching '{title}' by '{artist}' …")
    candidate = _fetch_lrclib_candidate(artist, title)
    if candidate and candidate.get("syncedLyrics"):
        print(f"  [LRCLIB] Found (id={candidate.get('id', '?')}, title={candidate.get('trackName', '?')})")
        return candidate["syncedLyrics"]
    if candidate:
        print("  [LRCLIB] No synced lyrics found.")
    return None


def fetch_plain_lyrics(artist: str, title: str) -> str | None:
    print(f"  [LRCLIB] Looking for plain lyrics for '{title}' by '{artist}' …")
    candidate = _fetch_lrclib_candidate(artist, title)
    if candidate and candidate.get("plainLyrics"):
        print("  [LRCLIB] Found plain lyrics.")
        return candidate["plainLyrics"]
    print("  [LRCLIB] No plain lyrics found.")
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

# ── Language dictionary lookups ──────────────────────────────────────────────

_or_lookup_fn     = None   # OpenRussian single-def (ru)
_or_lookup_all_fn = None   # OpenRussian all-defs   (ru)
_it_lookup_fn     = None   # Italian OMW single-def (it)
_it_lookup_all_fn = None   # Italian OMW all-defs   (it)


def _backend_dir() -> str:
    d = str((Path(__file__).parent.parent / "backend").resolve())
    if d not in sys.path:
        sys.path.insert(0, d)
    return d


def _load_openrussian() -> None:
    """Attempt to load the OpenRussian index from the backend cache."""
    global _or_lookup_fn, _or_lookup_all_fn
    _backend_dir()
    try:
        from openrussian import ensure_loaded, lookup, lookup_all  # type: ignore[import]
        ensure_loaded()
        _or_lookup_fn = lookup
        _or_lookup_all_fn = lookup_all
        print("  [OpenRussian] Dictionary loaded.")
    except Exception as exc:
        print(f"  [OpenRussian] Could not load dictionary: {exc}")
        _or_lookup_fn = None
        _or_lookup_all_fn = None


def _load_italian_dict() -> None:
    """Load Italian OMW dictionary from the backend module."""
    global _it_lookup_fn, _it_lookup_all_fn
    _backend_dir()
    try:
        from italian_dict import ensure_loaded, lookup, lookup_all  # type: ignore[import]
        ensure_loaded()
        _it_lookup_fn = lookup
        _it_lookup_all_fn = lookup_all
    except Exception as exc:
        print(f"  [Italian dict] Could not load: {exc}")
        _it_lookup_fn = None
        _it_lookup_all_fn = None


def _rank_definitions(candidates: list[str], translation: str) -> list[str]:
    """Re-order candidates so the one with most word-overlap with `translation` comes first.

    Only content words longer than 2 characters are counted to avoid noise
    from common stop-words ('a', 'to', 'of', …).
    """
    if not translation or len(candidates) <= 1:
        return candidates
    trans_words = set(re.findall(r'\w+', translation.lower()))
    trans_words = {w for w in trans_words if len(w) > 2}

    def _score(defn: str) -> int:
        return sum(1 for w in re.findall(r'\w+', defn.lower()) if w in trans_words)

    return sorted(candidates, key=_score, reverse=True)


def _resolve_definition(lemma: str, lang_code: str, translation: str = "") -> str:
    """Return the best English definition for the lemma.

    Fetches all candidate definitions from the appropriate dictionary, then
    ranks them by word-overlap with `translation` so the most contextually
    relevant meaning appears first.  Falls back to a stub if nothing is found.
    """
    clean_lemma = re.sub(r"[^\w]", "", lemma, flags=re.UNICODE)
    candidates: list[str] = []

    if lang_code == "ru" and _or_lookup_all_fn is not None:
        candidates = _or_lookup_all_fn(clean_lemma) or []
    elif lang_code == "it" and _it_lookup_all_fn is not None:
        candidates = _it_lookup_all_fn(clean_lemma) or []

    if not candidates:
        return f"[{clean_lemma}]"

    ranked = _rank_definitions(candidates, translation)
    return "; ".join(ranked)


# ── Backend push ──────────────────────────────────────────────────────────────

def push_to_backend(api_url: str, payload: dict, replace_id: int | None = None) -> None:
    """POST the processed song JSON to the FlowUp backend API.

    If replace_id is given, the existing song is deleted first so the new
    version takes its place (playlist associations are preserved on the
    new song ID — callers should re-add if needed).
    """
    base = api_url.rstrip("/")
    if replace_id is not None:
        print(f"\n[6/5] Deleting old song id={replace_id} …")
        try:
            d = requests.delete(f"{base}/api/songs/{replace_id}", timeout=10)
            if d.status_code in (204, 200):
                print(f"  [Backend] Deleted song {replace_id}.")
            else:
                print(f"  [Backend] Delete returned {d.status_code}: {d.text[:200]}")
        except requests.RequestException as exc:
            print(f"  [Backend] Delete error: {exc}")

    url = base + "/api/songs"
    print(f"\n[6/5] Pushing to backend: {url} …")
    try:
        r = requests.post(url, json=payload, timeout=90)
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
            "dictionary_definition": _resolve_definition(analysis.lemma, lang_code, translation),
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
            plain_lyrics = fetch_plain_lyrics(args.artist, args.title)
            if plain_lyrics and args.youtube_url:
                print("  [stable-ts] Falling back to forced alignment …")
                lrc = align_with_stable_ts(
                    plain_lyrics, args.youtube_url, args.lang,
                    model_name=args.stable_ts_model,
                )
    if lrc is None:
        print("ERROR: Could not retrieve synced lyrics. Aborting.")
        print("TIP:   Provide --youtube-url for stable-ts forced alignment.")
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

    # ── 3b. Language dictionaries
    if args.lang == "ru":
        _load_openrussian()
    elif args.lang == "it":
        _load_italian_dict()

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
        push_to_backend(args.api_url, output, replace_id=args.replace_id)

    print(f"\n✓  Done — {len(lines)} lines.")
    print(sep)


if __name__ == "__main__":
    main()
