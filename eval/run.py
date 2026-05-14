"""
Translation evaluation harness.

Picks a random Russian song (or a specific one), samples N lines,
runs Argos full-line translation (ru→tr), and prints a report showing
word-level coverage alongside the translated output.

Usage:
    python -m eval.run                          # random ru song, 10 lines
    python -m eval.run --song-id 150            # specific song
    python -m eval.run --src ru --tgt tr -n 15
    python -m eval.run --api https://singoling.com --src ru --tgt tr
"""

from __future__ import annotations

import argparse
import json
import random
import textwrap
import urllib.request
from dataclasses import dataclass, field
from typing import Optional


# ──────────────────────────────────────────────────────────────────────────────
# Data classes
# ──────────────────────────────────────────────────────────────────────────────

@dataclass
class WordEntry:
    display_form: str
    lemma: str
    grammar: str
    en_definition: Optional[str]          # EN definition from DB (legacy, not scored)
    tr_definitions: list = field(default_factory=list)  # TR from kaikki (filled during eval)

    @property
    def has_definition(self) -> bool:
        """True when at least one Turkish definition was found in the kaikki DB."""
        return bool(self.tr_definitions)


@dataclass
class LineEntry:
    position: int
    text: str
    words: list[WordEntry]
    argos_translation: str = ""

    @property
    def coverage(self) -> float:
        """Fraction of words that have a definition."""
        if not self.words:
            return 0.0
        return sum(1 for w in self.words if w.has_definition) / len(self.words)


@dataclass
class EvalReport:
    song_id: int
    song_title: str
    song_artist: str
    src: str
    tgt: str
    lines: list[LineEntry] = field(default_factory=list)


# ──────────────────────────────────────────────────────────────────────────────
# API helpers
# ──────────────────────────────────────────────────────────────────────────────

def _get(url: str) -> dict:
    with urllib.request.urlopen(url, timeout=15) as resp:
        return json.loads(resp.read())


def fetch_songs(api: str, lang: str) -> list[dict]:
    songs = _get(f"{api}/api/songs")
    return [s for s in songs if s.get("language_code") == lang]


def fetch_song(api: str, song_id: int) -> dict:
    return _get(f"{api}/api/songs/{song_id}")


# ──────────────────────────────────────────────────────────────────────────────
# Core evaluation
# ──────────────────────────────────────────────────────────────────────────────

def evaluate(api: str, src: str, tgt: str, n: int, song_id: Optional[int]) -> EvalReport:
    from eval.argos_translator import ArgosTranslator

    # 1. Pick song
    if song_id is None:
        candidates = fetch_songs(api, src)
        if not candidates:
            raise RuntimeError(f"No songs with language_code='{src}' found in DB.")
        summary = random.choice(candidates)
        song_id = summary["id"]

    print(f"Fetching song {song_id} …")
    data = fetch_song(api, song_id)

    report = EvalReport(
        song_id=song_id,
        song_title=data.get("title", "?"),
        song_artist=data.get("artist", "?"),
        src=src,
        tgt=tgt,
    )

    # 2. Collect lines that have words
    eligible = []
    for line in data.get("lines", []):
        text = line.get("original_line", "").strip()
        words_raw = line.get("words", [])
        if not text or not words_raw:
            continue
        words = [
            WordEntry(
                display_form=w.get("display_form", ""),
                lemma=w.get("lemma", ""),
                grammar=w.get("grammar", ""),
                en_definition=w.get("dictionary_definition"),
            )
            for w in words_raw
        ]
        eligible.append(LineEntry(position=line.get("position", 0), text=text, words=words))

    if not eligible:
        raise RuntimeError("Song has no lines with words — has it been processed by the pipeline?")

    sampled = random.sample(eligible, min(n, len(eligible)))
    sampled.sort(key=lambda l: l.position)

    # 3. Load Argos translator (full-line only)
    print(f"Loading Argos {src}→{tgt} model …")
    translator = ArgosTranslator(src, tgt)

    # 3b. Load Wiktionary TR per-word lookup (cached)
    from eval.wiktionary import WiktionaryLookup
    print("Loading Wiktionary ru→tr lookup (cached) …")
    lookup = WiktionaryLookup()

    # 4. Translate each line + populate per-word TR definitions
    print(f"Translating {len(sampled)} lines …\n")
    for line in sampled:
        line.argos_translation = translator.translate(line.text)
        for word in line.words:
            word.tr_definitions = lookup.lookup(word.lemma, grammar=word.grammar)
        report.lines.append(line)

    lookup.close()

    return report


# ──────────────────────────────────────────────────────────────────────────────
# Report rendering
# ──────────────────────────────────────────────────────────────────────────────

_WIDTH = 80
_SEP = "─" * _WIDTH


def _render_word(w: WordEntry) -> str:
    if w.tr_definitions:
        def_part = " / ".join(w.tr_definitions)
    else:
        def_part = "⚠ no TR definition"
    grammar_short = w.grammar.split(",")[0].strip() if w.grammar else ""
    return f"  {w.display_form:20s} [{grammar_short:10s}]  {def_part}"


def _render_line(idx: int, line: LineEntry, label: str = "") -> str:
    tag = f"  [{label}]" if label else ""
    pct = int(line.coverage * 100)
    header = f"\nLine {idx+1} (pos={line.position}, coverage={pct}%){tag}"
    src_part = f"  RU: {line.text}"
    tgt_part = f"  TR: {line.argos_translation}"
    words_part = "\n".join(_render_word(w) for w in line.words)
    return "\n".join([header, src_part, tgt_part, "", words_part])


def print_report(report: EvalReport) -> None:
    lines = report.lines
    covered_words = sum(w.has_definition for l in lines for w in l.words)
    total_words = sum(len(l.words) for l in lines)
    overall_pct = int(covered_words / total_words * 100) if total_words else 0

    sorted_by_cov = sorted(lines, key=lambda l: l.coverage)
    worst = sorted_by_cov[:2]
    best = sorted_by_cov[-2:]

    print(_SEP)
    print(f"  TRANSLATION EVALUATION REPORT  —  {report.src.upper()} → {report.tgt.upper()}")
    print(_SEP)
    print(f"  Song    : {report.song_artist} — {report.song_title}  (id={report.song_id})")
    print(f"  Lines   : {len(lines)} sampled")
    print(f"  Words   : {covered_words}/{total_words} with TR definitions ({overall_pct}%)")
    print(_SEP)

    print("\n── ALL SAMPLED LINES " + "─" * 59)
    for i, line in enumerate(lines):
        print(_render_line(i, line))

    print("\n\n── BEST EXAMPLES (highest word coverage) " + "─" * 38)
    for line in reversed(best):
        print(_render_line(lines.index(line), line, label="BEST"))

    print("\n── WORST EXAMPLES (lowest word coverage) " + "─" * 38)
    for line in worst:
        print(_render_line(lines.index(line), line, label="WORST"))

    print("\n" + _SEP)
    print(f"  Definition source  : en.wiktionary.org ru→tr lookup (cached in eval/data/wikt_cache.db)")
    print(f"  Line translation   : Argos Translate offline ({report.src}→{report.tgt})")
    print(_SEP)


# ──────────────────────────────────────────────────────────────────────────────
# Entry point
# ──────────────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate word definitions + line translation for a language pair.")
    parser.add_argument("--api", default="https://singoling.com", help="Base API URL")
    parser.add_argument("--src", default="ru", help="Source language code (default: ru)")
    parser.add_argument("--tgt", default="tr", help="Target language code (default: tr)")
    parser.add_argument("--song-id", type=int, default=None, help="Specific song ID (default: random)")
    parser.add_argument("-n", type=int, default=10, help="Number of lines to sample (default: 10)")
    args = parser.parse_args()

    report = evaluate(args.api, args.src, args.tgt, args.n, args.song_id)
    print_report(report)


if __name__ == "__main__":
    main()
