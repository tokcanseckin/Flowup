#!/usr/bin/env python3
"""
Local Whisper alignment test — downloads audio from YouTube, transcribes
with the best Whisper model, prints full transcript + performance metrics.

Usage:
    python test_whisper_alignment.py [--url URL] [--model MODEL] [--lang LANG]

Defaults:
    URL   = Браво "Верю Я" YouTube video
    MODEL = large-v3 (best quality)
    LANG  = ru
"""

import argparse
import subprocess
import sys
import tempfile
import time
from pathlib import Path


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


def separate_vocals(audio_path: Path, tmp_path: Path) -> Path:
    """Use Demucs htdemucs model to isolate vocals from the audio."""
    print("\n[Demucs] Separating vocals from instruments …")
    t0 = time.perf_counter()
    result = subprocess.run(
        [
            sys.executable, "-m", "demucs",
            "--two-stems", "vocals",
            "-n", "htdemucs",
            "-o", str(tmp_path / "demucs_out"),
            str(audio_path),
        ],
        capture_output=True,
        text=True,
        timeout=300,
    )
    elapsed = time.perf_counter() - t0
    if result.returncode != 0:
        print(f"[Demucs] FAILED:\n{result.stderr[-500:]}")
        return audio_path  # fall back to original

    # Demucs outputs: <out>/<model>/<stem>/<vocals.wav>
    vocals_candidates = list((tmp_path / "demucs_out").rglob("vocals.wav"))
    if not vocals_candidates:
        print("[Demucs] Could not find vocals.wav output, using original audio")
        return audio_path

    vocals_path = vocals_candidates[0]
    size_mb = vocals_path.stat().st_size / 1_048_576
    print(f"[Demucs] Vocal isolation done in {elapsed:.1f}s → {vocals_path.name} ({size_mb:.1f} MB)")
    return vocals_path


def transcribe(audio_path: Path, model_name: str, language: str) -> dict:
    try:
        from faster_whisper import WhisperModel
    except ImportError:
        print("[ERROR] faster-whisper not installed. Run:")
        print("  pip install faster-whisper")
        sys.exit(1)

    print(f"\n[Whisper] Loading model '{model_name}' …")
    t0 = time.perf_counter()

    # device="cpu", compute_type="int8" — efficient on Apple Silicon / CPU
    model = WhisperModel(model_name, device="cpu", compute_type="int8")
    load_time = time.perf_counter() - t0
    print(f"[Whisper] Model loaded in {load_time:.1f}s")

    print(f"[Whisper] Transcribing (language='{language}') …")
    t1 = time.perf_counter()
    segments, info = model.transcribe(
        str(audio_path),
        language=language,
        beam_size=5,
        word_timestamps=True,
        vad_filter=False,          # disabled — VAD filters out music/singing
    )
    # Materialise the generator so we can time it
    segments = list(segments)
    transcribe_time = time.perf_counter() - t1

    return {
        "segments": segments,
        "info": info,
        "load_time": load_time,
        "transcribe_time": transcribe_time,
    }


def print_report(result: dict, audio_path: Path) -> None:
    segments = result["segments"]
    info = result["info"]
    load_t = result["load_time"]
    tx_t = result["transcribe_time"]

    audio_duration = segments[-1].end if segments else info.duration

    print("\n" + "═" * 70)
    print("  WHISPER TRANSCRIPTION REPORT")
    print("═" * 70)
    print(f"  Detected language : {info.language} (confidence {info.language_probability:.1%})")
    print(f"  Audio duration    : {audio_duration:.1f}s ({audio_duration/60:.1f} min)")
    print(f"  Model load time   : {load_t:.1f}s")
    print(f"  Transcription time: {tx_t:.1f}s")
    rtf = tx_t / audio_duration if audio_duration else float('inf')
    print(f"  Realtime factor   : {rtf:.2f}x  (lower = faster; <1.0 = faster than realtime)")
    print(f"  Segments          : {len(segments)}")
    print("═" * 70)
    print("\n  FULL TRANSCRIPT (with timestamps)\n")

    for seg in segments:
        start = f"{seg.start:7.2f}s"
        end   = f"{seg.end:7.2f}s"
        print(f"  [{start} → {end}]  {seg.text.strip()}")

    print("\n" + "═" * 70)
    print("  WORD-LEVEL TIMESTAMPS (first 50 words)\n")
    count = 0
    for seg in segments:
        if seg.words:
            for w in seg.words:
                print(f"  {w.start:6.2f}s  {w.word.strip():<20}  (prob {w.probability:.2f})")
                count += 1
                if count >= 50:
                    break
        if count >= 50:
            print("  … (truncated)")
            break

    print("═" * 70)


def main():
    parser = argparse.ArgumentParser(description="Local Whisper alignment test")
    parser.add_argument(
        "--url",
        default="https://www.youtube.com/watch?v=dTzWGeOmb4Q",
        help="YouTube URL of the song",
    )
    parser.add_argument(
        "--model",
        default="large-v3",
        choices=["tiny", "base", "small", "medium", "large-v2", "large-v3", "large-v3-turbo"],
        help="Whisper model to use (default: large-v3)",
    )
    parser.add_argument("--lang", default="ru", help="Language code (default: ru)")
    args = parser.parse_args()

    with tempfile.TemporaryDirectory(prefix="whisper-test-") as tmp:
        tmp_path = Path(tmp)
        audio_path = tmp_path / "audio.mp3"

        t_total = time.perf_counter()

        download_audio(args.url, audio_path)
        vocal_path = separate_vocals(audio_path, tmp_path)
        result = transcribe(vocal_path, args.model, args.lang)
        print_report(result, audio_path)

        total = time.perf_counter() - t_total
        print(f"\n  Total wall-clock time: {total:.1f}s\n")


if __name__ == "__main__":
    main()
