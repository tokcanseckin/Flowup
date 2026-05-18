#!/usr/bin/env python3
"""
stable-ts forced-alignment test.

Fetches plain lyrics from LRCLIB for a given artist/title, downloads audio
from YouTube, then uses stable-ts (Whisper encoder) to force-align the known
lyrics — much more accurate than Aeneas TTS/DTW.

Usage:
    python test_stable_ts.py --url URL --artist ARTIST --title TITLE [--model MODEL] [--lang LANG]

Defaults:
    ARTIST = Браво
    TITLE  = Верю Я
    MODEL  = large-v3-turbo
    LANG   = ru
    URL    = https://www.youtube.com/watch?v=dTzWGeOmb4Q
"""

import argparse
import subprocess
import sys
import time
from pathlib import Path

import requests

DEFAULT_MODEL = "large-v3-turbo"
DEFAULT_URL = "https://www.youtube.com/watch?v=dTzWGeOmb4Q"
DEFAULT_ARTIST = "Браво"
DEFAULT_TITLE = "Верю Я"


def fetch_plain_lyrics(artist: str, title: str) -> list[str]:
    print(f"[LRCLIB] Searching plain lyrics for '{title}' by '{artist}' …")
    for params in [
        {"q": f"{artist} {title}"},
    ]:
        try:
            r = requests.get("https://lrclib.net/api/search", params=params, timeout=12)
            r.raise_for_status()
            for hit in r.json():
                if hit.get("plainLyrics"):
                    print(f"[LRCLIB] Found: {hit.get('trackName')} (id={hit.get('id')})")
                    lines = [l.strip() for l in hit["plainLyrics"].splitlines()
                             if l.strip() and l.strip() != "♪"]
                    return filter_metadata_lines(lines)
        except requests.RequestException as e:
            print(f"[LRCLIB] Error: {e}")
    # fallback: /api/get
    try:
        r2 = requests.get("https://lrclib.net/api/get",
                          params={"artist_name": artist, "track_name": title}, timeout=12)
        if r2.status_code == 200:
            body = r2.json()
            if body.get("plainLyrics"):
                lines = [l.strip() for l in body["plainLyrics"].splitlines()
                         if l.strip() and l.strip() != "♪"]
                print(f"[LRCLIB] Found via /api/get (id={body.get('id')})")
                return filter_metadata_lines(lines)
    except requests.RequestException as e:
        print(f"[LRCLIB] Error: {e}")
    print("[LRCLIB] No plain lyrics found — cannot proceed.")
    sys.exit(1)


def download_audio(youtube_url: str, out_path: Path) -> None:
    print(f"[yt-dlp] Downloading audio from:\n  {youtube_url}")
    cmd = [
        sys.executable, "-m", "yt_dlp",
        "-f", "bestaudio[acodec!=none]/best[acodec!=none]",
        "-x", "--audio-format", "mp3",
        "-o", str(out_path.with_suffix(".%(ext)s")),
        "--no-playlist",
        youtube_url,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0 or not out_path.exists():
        print("[yt-dlp] FAILED:")
        print(result.stderr[-1000:] or result.stdout[-1000:])
        sys.exit(1)
    size_mb = out_path.stat().st_size / 1_048_576
    print(f"[yt-dlp] Downloaded: {out_path.name} ({size_mb:.1f} MB)")


import unicodedata as _uc
import re as _re

_METADATA_LINE_PAT = _re.compile(r'^\(.*\)$')

def filter_metadata_lines(lines: list[str]) -> list[str]:
    """Remove parenthetical-only lines (e.g. composer credits like '(Ю. Антонов)')."""
    filtered = [l for l in lines if not _METADATA_LINE_PAT.match(l)]
    n_removed = len(lines) - len(filtered)
    if n_removed:
        print(f"[lyrics] Removed {n_removed} metadata line(s) (parenthetical-only)")
    return filtered


STOPWORDS = {"я", "и", "в", "на", "не", "но", "он", "её", "мне",
             "мой", "моя", "его", "мы", "вы", "ты", "да", "нет", "вот"}

def normalize(s: str) -> str:
    s = _uc.normalize("NFC", s.lower())
    return _re.sub(r"[^\w]", "", s, flags=_re.UNICODE)

def anchor_for(line_words: list[str]) -> tuple[int, list[str]]:
    """Return (offset_in_line, [anchor, next?]) — skip stopwords."""
    for i, w in enumerate(line_words):
        nw = normalize(w)
        if nw not in STOPWORDS and len(nw) >= 3:
            anchor = [nw]
            if i + 1 < len(line_words):
                anchor.append(normalize(line_words[i + 1]))
            return i, anchor
    return 0, [normalize(line_words[0])]

def collect_words(result) -> list[tuple[float, float, str]]:
    words = []
    for seg in result.segments:
        for w in (seg.words or []):
            t = w.word.strip()
            if t:
                words.append((w.start, w.end, t))
    return words

def match_lines(lyrics: list[str], words: list[tuple]) -> list:
    """Map each lyric line to (start_i, end_i, t_start, t_end) in words list."""
    out = []
    wi = 0
    for line in lyrics:
        lw = line.split()
        if not lw:
            out.append(None); continue
        offset, anchors = anchor_for(lw)
        found = None
        for i in range(wi, len(words)):
            if normalize(words[i][2]) == anchors[0]:
                if len(anchors) > 1 and i + 1 < len(words):
                    if normalize(words[i + 1][2]) != anchors[1]:
                        continue
                si = max(0, i - offset)
                ei = min(si + len(lw) - 1, len(words) - 1)
                found = (si, ei, words[si][0], words[ei][1])
                wi = i + 1
                break
        out.append(found)
    return out

def is_stacked(words: list[tuple], si: int, ei: int) -> bool:
    """True if 3+ words in [si..ei] share the same start time (compressed by stable-ts)."""
    from collections import Counter
    if ei <= si:
        return False
    times = [round(words[j][0], 2) for j in range(si, min(ei + 1, len(words)))]
    return max(Counter(times).values(), default=0) >= 3


def dedup_assignments(lyrics: list[str], assignments: list, all_words: list[tuple]) -> list[bool]:
    """Mark consecutive stacked lines that share the same timestamp as duplicates."""
    is_dup = [False] * len(lyrics)
    last_stacked_start = None
    for i, a in enumerate(assignments):
        if a and is_stacked(all_words, a[0], a[1]):
            if last_stacked_start is not None and abs(a[2] - last_stacked_start) < 0.5:
                is_dup[i] = True
            else:
                last_stacked_start = a[2]
        else:
            last_stacked_start = None
    return is_dup


def get_audio_duration(audio_path: Path) -> float:
    """Return audio duration in seconds via ffprobe."""
    result = subprocess.run(
        ["ffprobe", "-v", "quiet", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(audio_path)],
        capture_output=True, text=True,
    )
    try:
        return float(result.stdout.strip())
    except ValueError:
        return 0.0

def isolate_vocals(audio_path: Path, cache_dir: Path) -> Path:
    """Run Demucs htdemucs to isolate vocals. Returns cached vocals.wav path."""
    vocals_cache = cache_dir / "vocals.wav"
    if vocals_cache.exists():
        size_mb = vocals_cache.stat().st_size / 1_048_576
        print(f"[Demucs] Using cached vocals: {vocals_cache.name} ({size_mb:.1f} MB)")
        return vocals_cache

    print("[Demucs] Separating vocals from instruments …")
    t0 = time.perf_counter()
    demucs_out = cache_dir / "demucs_out"
    result = subprocess.run(
        [sys.executable, "-m", "demucs",
         "--two-stems", "vocals",
         "-n", "htdemucs",
         "-o", str(demucs_out),
         str(audio_path)],
        capture_output=True, text=True, timeout=600,
    )
    elapsed = time.perf_counter() - t0
    if result.returncode != 0:
        print(f"[Demucs] FAILED:\n{result.stderr[-500:]}")
        return audio_path

    candidates = list(demucs_out.rglob("vocals.wav"))
    if not candidates:
        print("[Demucs] vocals.wav not found — falling back to original")
        return audio_path

    vocals_path = candidates[0]
    vocals_path.rename(vocals_cache)
    size_mb = vocals_cache.stat().st_size / 1_048_576
    print(f"[Demucs] Done in {elapsed:.1f}s → vocals.wav ({size_mb:.1f} MB)")
    return vocals_cache

def crop_audio_ffmpeg(src: Path, start_s: float, dst: Path) -> None:
    subprocess.run(
        ["ffmpeg", "-y", "-i", str(src), "-ss", str(start_s), str(dst)],
        capture_output=True, check=True,
    )

def detect_vocal_segments(
    vocals_path: Path,
    cache_dir: Path,
    min_speech_dur: float = 2.0,
    merge_gap: float = 25.0,
) -> list[tuple[float, float]]:
    """Use silero-VAD to find vocal segments in vocals.wav. Results are cached."""
    import json
    cache = cache_dir / f"vad_segments_mg{int(merge_gap)}.json"
    if cache.exists():
        segs = [(s, e) for s, e in json.loads(cache.read_text())]
        print(f"[VAD] Using cached segments ({len(segs)} found):")
        for s, e in segs:
            print(f"      {s:.1f}s → {e:.1f}s  ({e-s:.1f}s)")
        return segs

    import torch
    print("[VAD] Loading silero-vad …")
    model, utils = torch.hub.load(
        "snakers4/silero-vad", "silero_vad", force_reload=False, trust_repo=True
    )
    get_speech_timestamps, _, read_audio, _, _ = utils

    wav = read_audio(str(vocals_path), sampling_rate=16000)
    raw = get_speech_timestamps(
        wav, model,
        sampling_rate=16000,
        min_speech_duration_ms=int(min_speech_dur * 1000),
        min_silence_duration_ms=300,
    )
    if not raw:
        print("[VAD] No speech detected — returning full file as single segment")
        return [(0.0, wav.shape[-1] / 16000)]

    # Convert samples → seconds
    SR = 16000
    segs = [(t["start"] / SR, t["end"] / SR) for t in raw]

    # Merge segments separated by < merge_gap seconds
    merged: list[tuple[float, float]] = [segs[0]]
    for s, e in segs[1:]:
        if s - merged[-1][1] < merge_gap:
            merged[-1] = (merged[-1][0], e)
        else:
            merged.append((s, e))

    # Drop segments shorter than min_speech_dur after merging
    merged = [(s, e) for s, e in merged if e - s >= min_speech_dur]

    cache.write_text(json.dumps(merged))
    print(f"[VAD] Found {len(merged)} vocal segments:")
    for s, e in merged:
        print(f"      {s:.1f}s → {e:.1f}s  ({e-s:.1f}s)")
    return merged


def distribute_lines(
    lyrics: list[str],
    segments: list[tuple[float, float]],
) -> list[list[str]]:
    """Assign lyrics lines to segments proportional to segment vocal duration."""
    if len(segments) == 1:
        return [lyrics]

    word_counts = [len(line.split()) for line in lyrics]
    total_words = sum(word_counts)
    total_dur = sum(e - s for s, e in segments)

    groups: list[list[str]] = [[] for _ in segments]
    line_idx = 0

    for seg_idx, (s, e) in enumerate(segments[:-1]):
        if line_idx >= len(lyrics):
            break
        target_words = total_words * (e - s) / total_dur
        collected = 0
        while line_idx < len(lyrics):
            wc = word_counts[line_idx]
            if collected > 0 and collected + wc > target_words + wc / 2:
                break
            groups[seg_idx].append(lyrics[line_idx])
            collected += wc
            line_idx += 1
        if not groups[seg_idx]:
            groups[seg_idx].append(lyrics[line_idx])
            line_idx += 1

    groups[-1] = lyrics[line_idx:]
    return groups


def align_segmented(
    vocals_path: Path,
    segments: list[tuple[float, float]],
    lyrics: list[str],
    model_name: str,
    language: str,
):
    """Align lyrics segment-by-segment: distribute lines proportionally, then
    align only those lines into their segment's cropped audio."""
    import stable_whisper

    print(f"\n[stable-ts] Loading model '{model_name}' …")
    t0 = time.perf_counter()
    model = stable_whisper.load_model(model_name)
    load_time = time.perf_counter() - t0
    print(f"[stable-ts] Model loaded in {load_time:.1f}s")

    groups = distribute_lines(lyrics, segments)
    print(f"\n[VAD] Line distribution across {len(segments)} segments:")
    for i, ((s, e), grp) in enumerate(zip(segments, groups)):
        print(f"  Seg {i+1}: {s:.1f}→{e:.1f}s  ({len(grp)} lines): "
              + (grp[0][:30] + "…" if grp else "(empty)"))

    cache_dir = vocals_path.parent
    all_words: list[tuple] = []
    align_time = 0.0
    MARGIN_START = 8.0   # look back to catch vocals VAD may have missed
    MARGIN_END   = 1.0

    for seg_idx, ((seg_start, seg_end), seg_lyrics) in enumerate(zip(segments, groups)):
        if not seg_lyrics:
            print(f"[stable-ts] Seg {seg_idx+1}: no lines assigned — skipping")
            continue

        # Don't extend start into the previous segment's territory
        prev_seg_end = segments[seg_idx - 1][1] if seg_idx > 0 else 0.0
        crop_start = max(prev_seg_end, seg_start - MARGIN_START)
        crop_end = seg_end + MARGIN_END
        crop_path = cache_dir / f".crop_seg_{seg_idx}.wav"

        subprocess.run(
            ["ffmpeg", "-y", "-i", str(vocals_path),
             "-ss", str(crop_start), "-to", str(crop_end),
             str(crop_path)],
            capture_output=True, check=True,
        )

        print(f"\n[stable-ts] Seg {seg_idx+1}/{len(segments)}: "
              f"aligning {len(seg_lyrics)} lines in {seg_start:.1f}→{seg_end:.1f}s …")

        MAX_TRIMS = 3
        for trim in range(MAX_TRIMS + 1):
            seg_words, seg_align_time = _ngap_loop(
                model, crop_path, seg_lyrics, language,
                cache_dir=cache_dir,
                offset_abs=crop_start,
                gap_threshold=7.0,
                lookback=2.0,
            )
            align_time += seg_align_time

            if len(seg_lyrics) <= 1 or trim == MAX_TRIMS:
                break

            # Check if last assigned line is still stacked
            words_rel_seg = [(s - crop_start, e - crop_start, w) for s, e, w in seg_words]
            assignments_seg = match_lines(seg_lyrics, words_rel_seg)
            last_a = assignments_seg[-1]
            if last_a and is_stacked(words_rel_seg, last_a[0], last_a[1]):
                spilled = seg_lyrics[-1]
                seg_lyrics = seg_lyrics[:-1]
                if seg_idx + 1 < len(groups):
                    groups[seg_idx + 1] = [spilled] + groups[seg_idx + 1]
                print(f"[stable-ts] Seg {seg_idx+1}: last line stacked → "
                      f"spilled to seg {seg_idx+2}, retrying with {len(seg_lyrics)} lines …")
            else:
                break

        all_words += seg_words

    return all_words, load_time, align_time

def _ngap_loop(
    model,
    audio_path: Path,
    lyrics: list[str],
    language: str,
    cache_dir: Path,
    offset_abs: float = 0.0,
    gap_threshold: float = 15.0,
    lookback: float = 5.0,
) -> tuple[list[tuple], float]:
    """Core N-gap alignment loop. Works on any audio file (full song or segment crop).

    offset_abs: absolute timestamp of audio_path's t=0 in the original song.
    Returns (words_abs, align_time) where words_abs timestamps are absolute.
    """
    all_words: list[tuple] = []
    committed = 0
    sub_start = 0.0       # how far into audio_path we've cropped so far
    consecutive_no_progress = 0
    align_time = 0.0
    MAX_PASSES = 10
    MAX_LINE_DENSITY = 0.5   # lines/s — above this, alignment reliably fails
    audio_total = get_audio_duration(audio_path)

    for pass_num in range(1, MAX_PASSES + 1):
        remaining = lyrics[committed:]
        if not remaining:
            break

        # Density guard: stop if remaining lines can't possibly fit in remaining audio
        audio_remaining = max(audio_total - sub_start, 1.0)
        density = len(remaining) / audio_remaining
        if density > MAX_LINE_DENSITY:
            print(f"    pass {pass_num}: ⚠ DENSITY TOO HIGH "
                  f"({len(remaining)} lines / {audio_remaining:.0f}s = {density:.2f}/s) "
                  f"— stopping to avoid stacking")
            break

        if sub_start > 0:
            sub_crop = cache_dir / f".ngap_{audio_path.stem}_{int(offset_abs + sub_start)}.wav"
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

        words_rel = collect_words(result)
        abs_off = offset_abs + sub_start
        words_abs = [(s + abs_off, e + abs_off, w) for s, e, w in words_rel]
        assignments = match_lines(remaining, words_rel)

        first_bad = None
        for i, a in enumerate(assignments):
            if a and is_stacked(words_rel, a[0], a[1]):
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
            gap_abs_s = abs_off + gap_start_rel
            gap_abs_e = abs_off + gap_end_rel
            print(f"    pass {pass_num}: gap {gap_abs_s:.1f}→{gap_abs_e:.1f}s — "
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


def align_with_split(audio_path: Path, lyrics: list[str], model_name: str, language: str):
    import stable_whisper

    print(f"\n[stable-ts] Loading model '{model_name}' …")
    t0 = time.perf_counter()
    model = stable_whisper.load_model(model_name)
    load_time = time.perf_counter() - t0
    print(f"[stable-ts] Model loaded in {load_time:.1f}s")

    print(f"[stable-ts] Aligning {len(lyrics)} lines …")
    all_words, align_time = _ngap_loop(
        model, audio_path, lyrics, language,
        cache_dir=audio_path.parent,
        offset_abs=0.0,
        gap_threshold=15.0,
        lookback=25.0,
    )
    return all_words, load_time, align_time


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default=DEFAULT_URL, help="YouTube URL of the song")
    parser.add_argument("--artist", default=DEFAULT_ARTIST)
    parser.add_argument("--title", default=DEFAULT_TITLE)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--lang", default="ru")
    parser.add_argument("--demucs", action="store_true", help="Isolate vocals with Demucs before alignment")
    parser.add_argument("--vad", action="store_true", help="Use silero-VAD segmentation (implies --demucs)")
    parser.add_argument("--merge-gap", type=float, default=25.0, dest="merge_gap", help="VAD merge gap in seconds (default 25)")
    args = parser.parse_args()

    lyrics = fetch_plain_lyrics(args.artist, args.title)
    print(f"[lyrics] {len(lyrics)} lines fetched")

    # Per-song cache directory so songs don't clobber each other
    import re
    slug = re.sub(r"[^\w]+", "_", f"{args.artist}_{args.title}").strip("_").lower()
    cache_dir = Path(__file__).parent / ".test_cache" / slug
    cache_dir.mkdir(parents=True, exist_ok=True)
    audio_path = cache_dir / "audio.mp3"

    t_total = time.perf_counter()
    if audio_path.exists():
        print(f"[yt-dlp] Using cached audio: {audio_path} ({audio_path.stat().st_size/1_048_576:.1f} MB)")
    else:
        download_audio(args.url, audio_path)

    align_src = audio_path
    if args.vad or args.demucs:
        align_src = isolate_vocals(audio_path, cache_dir)

    if args.vad:
        segments = detect_vocal_segments(align_src, cache_dir, merge_gap=args.merge_gap)
        if len(segments) > 1:
            all_words, load_time, align_time = align_segmented(align_src, segments, lyrics, args.model, args.lang)
        else:
            print("[VAD] Only 1 segment detected — falling back to normal alignment")
            all_words, load_time, align_time = align_with_split(align_src, lyrics, args.model, args.lang)
    else:
        all_words, load_time, align_time = align_with_split(align_src, lyrics, args.model, args.lang)
    total_time = time.perf_counter() - t_total

    # ── Match words to lines ──────────────────────────────────────────────────
    assignments = match_lines(lyrics, all_words)
    dups = dedup_assignments(lyrics, assignments, all_words)

    # ── Print results ─────────────────────────────────────────────────────────
    width = 72
    print()
    print("═" * width)
    print("  stable-ts FORCED ALIGNMENT  (per-line, word-reconstructed)")
    print("═" * width)
    print(f"  {'start':>7}  {'end':>7}  line")
    print(f"  {'-'*7}  {'-'*7}  {'-'*52}")

    n_ok = 0
    n_stacked = 0
    n_deduped = 0
    n_missing = 0
    for i, (a, line) in enumerate(zip(assignments, lyrics)):
        if dups[i]:
            print(f"  {'[dup]':>7}  {'':>7}  {line} ⚠")
            n_deduped += 1
        elif a:
            stacked = is_stacked(all_words, a[0], a[1])
            s = f"{a[2]:.1f}s"
            e = f"{a[3]:.1f}s"
            flag = " ⚠" if stacked else ""
            print(f"  {s:>7}  {e:>7}  {line}{flag}")
            if stacked:
                n_stacked += 1
            else:
                n_ok += 1
        else:
            print(f"  {'  ???':>7}  {'  ???':>7}  {line} ⚠")
            n_missing += 1

    print("═" * width)
    total_lines = len(lyrics)
    print(f"  Lines      : {n_ok}/{total_lines} clean  "
          f"| {n_stacked} stacked  | {n_deduped} deduped  | {n_missing} missing")
    print(f"  Model load : {load_time:.1f}s")
    print(f"  Align time : {align_time:.1f}s")
    print(f"  Total      : {total_time:.1f}s")
    print("═" * width)
    print()


if __name__ == "__main__":
    main()
