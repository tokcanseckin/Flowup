"""FlowUp Alignment Worker — stable-ts forced alignment module.

Self-contained: no imports from the pipeline or backend directories.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import tempfile
import time
import unicodedata
from collections import Counter
from pathlib import Path

# ── Constants ──────────────────────────────────────────────────────────────────

MAX_LINE_DENSITY = 0.5   # lines/s — density guard threshold (stop N-gap loop above this)
MAX_PASSES       = 10

_METADATA_PAT = re.compile(r'^\(.*\)$')
_SECTION_PAT  = re.compile(r'^\[[^\]]+\]$')

_STOPWORDS = {
    "я", "и", "в", "на", "не", "но", "он", "её", "мне",
    "мой", "моя", "его", "мы", "вы", "ты", "да", "нет", "вот",
}

# ── Lyric preprocessing ────────────────────────────────────────────────────────


def prepare_lyrics(plain_text: str) -> list[str]:
    """Strip blank lines, [Section] markers, and parenthetical metadata lines."""
    lines: list[str] = []
    for raw in plain_text.splitlines():
        line = raw.strip()
        if not line:
            continue
        if _SECTION_PAT.match(line):       # [Chorus], [Verse 1], etc.
            continue
        if _METADATA_PAT.match(line):      # (Автор — Композитор)
            print(f"  [alignment] Removed metadata line: {line[:60]}")
            continue
        lines.append(line)
    return lines


# ── Word-level helpers ─────────────────────────────────────────────────────────


def _norm(s: str) -> str:
    s = unicodedata.normalize("NFC", s.lower())
    return re.sub(r"[^\w]", "", s, flags=re.UNICODE)


def _anchor_for(lw: list[str]) -> tuple[int, list[str]]:
    """Pick a non-stopword anchor word (+ optional next word) for line matching."""
    for i, w in enumerate(lw):
        nw = _norm(w)
        if nw not in _STOPWORDS and len(nw) >= 3:
            anchor = [nw]
            if i + 1 < len(lw):
                anchor.append(_norm(lw[i + 1]))
            return i, anchor
    return 0, [_norm(lw[0])]


def collect_words(result) -> list[tuple[float, float, str]]:
    """Extract (start, end, word) tuples from a stable-ts alignment result."""
    words: list[tuple[float, float, str]] = []
    for seg in result.segments:
        for w in (seg.words or []):
            t = w.word.strip()
            if t:
                words.append((w.start, w.end, t))
    return words


def match_lines(
    lyrics: list[str],
    words: list[tuple[float, float, str]],
) -> list[tuple[int, int, float, float] | None]:
    """Map each lyric line to (start_word_idx, end_word_idx, t_start, t_end)."""
    out: list[tuple[int, int, float, float] | None] = []
    wi = 0
    for line in lyrics:
        lw = line.split()
        if not lw:
            out.append(None)
            continue
        offset, anchors = _anchor_for(lw)
        found: tuple[int, int, float, float] | None = None
        for i in range(wi, len(words)):
            if _norm(words[i][2]) == anchors[0]:
                if len(anchors) > 1 and i + 1 < len(words):
                    if _norm(words[i + 1][2]) != anchors[1]:
                        continue
                si = max(0, i - offset)
                ei = min(si + len(lw) - 1, len(words) - 1)
                found = (si, ei, words[si][0], words[ei][1])
                wi = i + 1
                break
        out.append(found)
    return out


def is_stacked(words: list[tuple], si: int, ei: int) -> bool:
    """True when 3+ words in the range share the same start time (stable-ts compression)."""
    if ei <= si:
        return False
    times = [round(words[j][0], 2) for j in range(si, min(ei + 1, len(words)))]
    return max(Counter(times).values(), default=0) >= 3


# ── Audio utilities ────────────────────────────────────────────────────────────


def audio_duration(audio_path: Path) -> float:
    r = subprocess.run(
        ["ffprobe", "-v", "quiet", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(audio_path)],
        capture_output=True, text=True,
    )
    try:
        return float(r.stdout.strip())
    except ValueError:
        return 0.0


# ── Demucs vocal isolation ─────────────────────────────────────────────────────


def isolate_vocals(audio_path: Path, work_dir: Path) -> Path:
    """Run Demucs htdemucs --two-stems=vocals. Returns path to vocals.wav."""
    print("  [alignment] Isolating vocals with Demucs …")
    t0 = time.perf_counter()
    demucs_out = work_dir / "demucs_out"
    r = subprocess.run(
        [sys.executable, "-m", "demucs",
         "--two-stems", "vocals", "-n", "htdemucs",
         "-o", str(demucs_out), str(audio_path)],
        capture_output=True, text=True, timeout=600,
    )
    if r.returncode != 0:
        print(f"  [alignment] Demucs failed — using original audio\n{r.stderr[-300:]}")
        return audio_path
    candidates = list(demucs_out.rglob("vocals.wav"))
    if not candidates:
        print("  [alignment] vocals.wav not found — using original audio")
        return audio_path
    print(f"  [alignment] Demucs done in {time.perf_counter() - t0:.1f}s")
    return candidates[0]


# ── N-gap alignment loop ───────────────────────────────────────────────────────


def _ngap_loop(
    model,
    audio_path: Path,
    lyrics: list[str],
    language: str,
    work_dir: Path,
    offset_abs: float = 0.0,
    gap_threshold: float = 15.0,
    lookback: float = 25.0,
) -> tuple[list[tuple], float]:
    """
    Iterative forced alignment with N-gap splitting and density guard.

    On each pass:
      1. Run stable-ts on the remaining (unaligned) portion of the audio.
      2. If no stacking issues → commit all remaining lines and stop.
      3. If stacking found → find a major instrumental gap just before the
         stacking point, commit the clean lines before the gap, crop the
         audio to just after the gap, and repeat.
      4. Density guard: if too many lines remain for too little audio, stop
         early and emit nothing for those lines (avoids catastrophic stacking).
    """
    all_words: list[tuple] = []
    committed = 0
    sub_start = 0.0
    consecutive_no_progress = 0
    align_time = 0.0
    audio_total = audio_duration(audio_path)

    for pass_num in range(1, MAX_PASSES + 1):
        remaining = lyrics[committed:]
        if not remaining:
            break

        audio_remaining = max(audio_total - sub_start, 1.0)
        density = len(remaining) / audio_remaining
        if density > MAX_LINE_DENSITY:
            print(
                f"  [alignment] pass {pass_num}: ⚠ density guard "
                f"({len(remaining)} lines / {audio_remaining:.0f}s = {density:.2f}/s) — stopping"
            )
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

        words_rel = collect_words(result)
        abs_off = offset_abs + sub_start
        words_abs = [(s + abs_off, e + abs_off, w) for s, e, w in words_rel]
        assignments = match_lines(remaining, words_rel)

        # Find first stacked assignment
        first_bad: int | None = None
        for i, a in enumerate(assignments):
            if a and is_stacked(words_rel, a[0], a[1]):
                first_bad = i
                break

        if first_bad is None:
            # Clean pass — commit everything
            first_wi = assignments[0][0] if assignments[0] else 0
            all_words += words_abs[first_wi:]
            committed = len(lyrics)
            break

        bad_time_rel = assignments[first_bad][2]

        # Find the largest gap before the stacking point
        major_gap: tuple[float, float] | None = None
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

        # Count cleanly aligned lines before the gap
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
            print(
                f"  [alignment] pass {pass_num}: gap "
                f"{abs_off + gap_start_rel:.1f}→{abs_off + gap_end_rel:.1f}s — "
                f"committed {committed}/{len(lyrics)} lines"
            )
        else:
            consecutive_no_progress += 1
            if consecutive_no_progress >= 2:
                first_wi = assignments[0][0] if assignments[0] else 0
                all_words += words_abs[first_wi:]
                committed = len(lyrics)
                break

        sub_start = new_sub_start

    return all_words, align_time


# ── LRC builder ────────────────────────────────────────────────────────────────


def _format_lrc_ts(ms: int) -> str:
    total = max(0, ms)
    m  = total // 60_000
    s  = (total % 60_000) // 1_000
    cs = (total % 1_000) // 10
    return f"{m:02}:{s:02}.{cs:02}"


def words_to_lrc(lyrics: list[str], all_words: list[tuple]) -> str:
    """Convert aligned word list + lyric lines to an LRC string."""
    assignments = match_lines(lyrics, all_words)
    rows: list[tuple[int, str]] = []
    last_stacked_start: float | None = None

    for a, line in zip(assignments, lyrics):
        if not a:
            print(f"  [alignment] ⚠ unaligned (skipped): {line[:60]}")
            continue
        stacked = is_stacked(all_words, a[0], a[1])
        if stacked:
            if last_stacked_start is not None and abs(a[2] - last_stacked_start) < 0.5:
                print(f"  [alignment] ⚠ duplicate skipped: {line[:60]}")
                continue
            last_stacked_start = a[2]
        else:
            last_stacked_start = None
        rows.append((int(a[2] * 1000), line))

    if not rows:
        return ""

    lrc_lines = [f"[{_format_lrc_ts(start_ms)}]{text}" for start_ms, text in rows]
    return "\n".join(lrc_lines)


# ── Public interface ───────────────────────────────────────────────────────────


def align_song(
    youtube_url: str,
    artist: str,
    title: str,
    lang: str,
    lyrics_text: str,
    model_name: str = "large-v3-turbo",
) -> str | None:
    """
    Download audio from YouTube, isolate vocals with Demucs, align the given
    plain-text lyrics using stable-ts, and return an LRC string.

    Returns None on any failure (download error, alignment produces no result, etc.).

    Args:
        youtube_url:  YouTube video URL.
        artist:       Artist name (for logging).
        title:        Track title (for logging).
        lang:         BCP-47 language code for stable-ts (e.g. 'ru', 'uk', 'es').
        lyrics_text:  Plain-text lyrics, one line per line (not LRC format).
        model_name:   stable-ts / Whisper model name.
    """
    lyric_lines = prepare_lyrics(lyrics_text)
    if not lyric_lines:
        print("  [alignment] No usable lyric lines after filtering.")
        return None

    try:
        import stable_whisper  # type: ignore[import-untyped]
    except ImportError:
        print("  [alignment] stable-ts not installed — run install.sh")
        return None

    with tempfile.TemporaryDirectory(prefix="flowup-worker-") as tmp_dir:
        tmp = Path(tmp_dir)
        audio_path = tmp / "audio.mp3"

        # ── Download audio ────────────────────────────────────────────────────
        print(f"  [alignment] Downloading: {artist} — {title}")
        yt_cmd = [
            sys.executable, "-m", "yt_dlp",
            "-f", "bestaudio[acodec!=none]/best[acodec!=none]",
            "-x", "--audio-format", "mp3",
            "-o", str(tmp / "audio.%(ext)s"),
            "--no-playlist",
            "--quiet",
        ]
        cookies = os.environ.get("YOUTUBE_COOKIES_FILE", "")
        if cookies and Path(cookies).exists():
            yt_cmd += ["--cookies", cookies]
        yt_cmd.append(youtube_url)

        dl = subprocess.run(yt_cmd, capture_output=True, text=True, timeout=180)
        if dl.returncode != 0 or not audio_path.exists():
            print(f"  [alignment] Download failed: {(dl.stderr or dl.stdout).strip()[:400]}")
            return None

        # ── Isolate vocals ────────────────────────────────────────────────────
        vocals = isolate_vocals(audio_path, tmp)

        # ── Load model ────────────────────────────────────────────────────────
        print(f"  [alignment] Loading model '{model_name}' …")
        t0 = time.perf_counter()
        model = stable_whisper.load_model(model_name)
        print(f"  [alignment] Model loaded in {time.perf_counter() - t0:.1f}s")

        # ── Align ─────────────────────────────────────────────────────────────
        print(f"  [alignment] Aligning {len(lyric_lines)} lines …")
        all_words, align_time = _ngap_loop(
            model, vocals, lyric_lines, lang,
            work_dir=tmp, offset_abs=0.0,
            gap_threshold=15.0, lookback=25.0,
        )
        print(f"  [alignment] Done in {align_time:.1f}s ({len(all_words)} words)")

        lrc = words_to_lrc(lyric_lines, all_words)
        if not lrc:
            print("  [alignment] No lines aligned.")
            return None

        print(f"  [alignment] LRC produced ({len(lrc)} chars)")
        return lrc
