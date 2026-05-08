#!/usr/bin/env python3
"""
FlowUp Alignment Worker

Polls the remote FlowUp backend for pending alignment tasks, runs stable-ts
forced alignment on the Mac Mini's local GPU/CPU, and submits the resulting
LRC back to the server. The backend then runs translation + NLP and stores
the finished song.

Usage:
    # First time:
    bash install.sh
    cp .env.example .env && nano .env  # fill in REMOTE_API_URL and WORKER_API_KEY

    # Start the worker:
    .venv/bin/python worker.py

    # Or run once (process any pending tasks, then exit):
    .venv/bin/python worker.py --once
"""

from __future__ import annotations

import argparse
import logging
import os
import subprocess
import sys
import time
from pathlib import Path

import requests
from dotenv import load_dotenv

# Load .env from same directory as this script
load_dotenv(Path(__file__).parent / ".env")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [worker] %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("worker")

# ── Config ─────────────────────────────────────────────────────────────────────

REMOTE_URL  = os.environ.get("REMOTE_API_URL", "").rstrip("/")
API_KEY     = os.environ.get("WORKER_API_KEY", "")
POLL_SECS   = int(os.environ.get("POLL_INTERVAL", "30"))
MODEL       = os.environ.get("STABLE_TS_MODEL", "large-v3-turbo")

if not REMOTE_URL:
    sys.exit("ERROR: REMOTE_API_URL is not set. Copy .env.example to .env and fill it in.")
if not API_KEY:
    sys.exit("ERROR: WORKER_API_KEY is not set. Copy .env.example to .env and fill it in.")

_HEADERS = {
    "X-Worker-Api-Key": API_KEY,
    "Content-Type": "application/json",
}

# ── LRCLIB fallback ────────────────────────────────────────────────────────────


def _fetch_plain_lyrics(artist: str, title: str) -> str | None:
    """Try to retrieve plain lyrics from LRCLIB when the task doesn't include them."""
    try:
        r = requests.get(
            "https://lrclib.net/api/search",
            params={"q": f"{artist} {title}"},
            timeout=12,
        )
        r.raise_for_status()
        for hit in r.json():
            if hit.get("plainLyrics"):
                return hit["plainLyrics"]

        r2 = requests.get(
            "https://lrclib.net/api/get",
            params={"artist_name": artist, "track_name": title},
            timeout=12,
        )
        if r2.status_code == 200:
            body = r2.json()
            if isinstance(body, dict) and body.get("plainLyrics"):
                return body["plainLyrics"]
    except requests.RequestException as exc:
        log.warning(f"LRCLIB error: {exc}")
    return None


# ── API calls ──────────────────────────────────────────────────────────────────


def poll_next_task() -> dict | None:
    """
    Fetch the next pending task from the server.

    The server atomically marks the task as 'processing' so no two workers
    pick up the same task. Returns None (HTTP 204) when the queue is empty.
    """
    r = requests.get(
        f"{REMOTE_URL}/api/worker/tasks/next",
        headers=_HEADERS,
        timeout=15,
    )
    if r.status_code == 204:
        return None
    r.raise_for_status()
    return r.json()


def submit_result(task_id: int, lrc: str) -> None:
    r = requests.post(
        f"{REMOTE_URL}/api/worker/tasks/{task_id}/result",
        headers=_HEADERS,
        json={"lrc": lrc},
        timeout=30,
    )
    r.raise_for_status()
    log.info(f"Task {task_id}: result submitted ({len(lrc)} chars of LRC)")


def submit_error(task_id: int, error: str) -> None:
    r = requests.post(
        f"{REMOTE_URL}/api/worker/tasks/{task_id}/result",
        headers=_HEADERS,
        json={"error": error[:500]},
        timeout=30,
    )
    r.raise_for_status()
    log.info(f"Task {task_id}: error reported — {error[:120]}")


# ── Task processing ────────────────────────────────────────────────────────────


def process_task(task: dict) -> None:
    """
    Run the full alignment pipeline for one task:
      1. Resolve plain lyrics (from task payload or LRCLIB).
      2. Download audio via yt-dlp.
      3. Isolate vocals with Demucs.
      4. Run stable-ts forced alignment (N-gap loop).
      5. Build LRC string and submit to the server.
    """
    from alignment import align_song  # imported here so startup is fast

    task_id = task["id"]
    artist  = task["artist"]
    title   = task["title"]
    lang    = task.get("lang", "ru")
    youtube = task["youtube_url"]
    lyrics  = (task.get("plain_lyrics") or "").strip()

    if not lyrics:
        log.info(f"Task {task_id}: no lyrics in payload — fetching from LRCLIB …")
        lyrics = _fetch_plain_lyrics(artist, title) or ""

    if not lyrics:
        raise RuntimeError(
            "No plain lyrics available (not provided in task and not found on LRCLIB)"
        )

    log.info(f"Task {task_id}: starting alignment ({len(lyrics.splitlines())} lyric lines)")

    lrc = align_song(
        youtube_url=youtube,
        artist=artist,
        title=title,
        lang=lang,
        lyrics_text=lyrics,
        model_name=MODEL,
    )

    if not lrc:
        raise RuntimeError("Alignment returned no result")

    submit_result(task_id, lrc)


# ── Auto-update ────────────────────────────────────────────────────────────────

_REPO_DIR = Path(__file__).resolve().parent.parent  # root of the git repo
_REQUIREMENTS = Path(__file__).resolve().parent / "requirements.txt"
_last_known_commit: str = ""


def _git(*args: str) -> str:
    r = subprocess.run(
        ["git", *args], cwd=str(_REPO_DIR),
        capture_output=True, text=True, timeout=30,
    )
    return r.stdout.strip()


def _check_for_updates() -> None:
    """Pull latest code from git; if the commit changed, reinstall deps and re-exec."""
    global _last_known_commit
    try:
        if not _last_known_commit:
            _last_known_commit = _git("rev-parse", "HEAD")

        _git("fetch", "--quiet")
        remote_hash = _git("rev-parse", "@{u}")

        if remote_hash and remote_hash != _last_known_commit:
            log.info(f"New commit detected ({remote_hash[:8]}) — pulling and restarting …")
            _git("pull", "--ff-only", "--quiet")
            subprocess.run(
                [sys.executable, "-m", "pip", "install", "-q",
                 "-r", str(_REQUIREMENTS)],
                check=False, timeout=300,
            )
            log.info("Restarting worker with updated code …")
            os.execv(sys.executable, [sys.executable] + sys.argv)
    except Exception as exc:
        log.debug(f"Auto-update check failed (non-fatal): {exc}")


# ── Main loop ──────────────────────────────────────────────────────────────────


def run(once: bool = False) -> None:
    log.info(
        f"Starting — remote: {REMOTE_URL} | poll: {POLL_SECS}s | model: {MODEL}"
    )

    while True:
        processed = 0
        try:
            task = poll_next_task()
            if task is None:
                if once:
                    log.info("Queue empty. Exiting (--once mode).")
                    return
                log.info("No pending tasks. Sleeping …")
            else:
                task_id = task["id"]
                log.info(
                    f"Task {task_id}: '{task['artist']} — {task['title']}' (lang={task.get('lang','?')})"
                )
                try:
                    process_task(task)
                    processed += 1
                    log.info(f"Task {task_id}: ✓ done")
                except Exception as exc:
                    log.error(f"Task {task_id}: ✗ {exc}")
                    try:
                        submit_error(task_id, str(exc))
                    except Exception as sub_exc:
                        log.error(f"Task {task_id}: could not submit error — {sub_exc}")

                if once:
                    return

        except requests.RequestException as exc:
            log.error(f"Poll error: {exc}")
        except Exception as exc:
            log.error(f"Unexpected error: {exc}")

        _check_for_updates()
        time.sleep(POLL_SECS)


def main() -> None:
    parser = argparse.ArgumentParser(description="FlowUp alignment worker")
    parser.add_argument(
        "--once",
        action="store_true",
        help="Process one pending task (or exit immediately if none) instead of looping",
    )
    args = parser.parse_args()
    run(once=args.once)


if __name__ == "__main__":
    main()
