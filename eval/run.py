"""
Translation evaluation harness.

Picks a random song (or a specific one), samples N lines, optionally
translates them, and prints a word-coverage report.

The lookup method and (optionally) the line translator are loaded from a
*pipeline* folder, so this harness is independent of both the language pair
and the method being tested.

Each pipeline folder (e.g. eval/pipelines/ru-tr/kaikki_1/) must contain:
  lookup.py   — class Lookup(src, tgt) with .lookup(lemma, grammar) -> list[str]
                and .close()
  translate.py (optional) — class Translator(src, tgt) with .translate(text) -> str
                and .close()

Usage:
    python -m eval.run --pipeline ru-tr/kaikki_1
    python -m eval.run --pipeline ru-tr/wiktionary_1 --song-id 150
    python -m eval.run --pipeline ru-tr/kaikki_1 --src ru --tgt tr -n 15
    python -m eval.run --pipeline ru-tr/kaikki_1 --api https://singoling.com
"""

from __future__ import annotations

import argparse
import importlib
import json
import random
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
    pipeline: str = ""
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

def _load_pipeline(pipeline: str, src: str, tgt: str):
    """Dynamically import Lookup (required) and Translator (optional) from a pipeline folder."""
    module_base = "eval.pipelines." + pipeline.replace("/", ".").replace("-", "_")

    lookup_mod = importlib.import_module(f"{module_base}.lookup")
    lookup = lookup_mod.Lookup(src, tgt)

    translator = None
    try:
        translate_mod = importlib.import_module(f"{module_base}.translate")
        translator = translate_mod.Translator(src, tgt)
        print(f"Loaded translator from {pipeline}/translate.py")
    except ModuleNotFoundError:
        pass  # line translation is optional

    return lookup, translator


def evaluate(api: str, src: str, tgt: str, n: int, song_id: Optional[int], pipeline: str) -> EvalReport:
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
        pipeline=pipeline,
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

    # 3. Load pipeline plugins
    print(f"Loading pipeline '{pipeline}' …")
    lookup, translator = _load_pipeline(pipeline, src, tgt)

    # 4. Translate each line (if translator available) + populate per-word definitions
    print(f"Processing {len(sampled)} lines …\n")
    for line in sampled:
        if translator is not None:
            line.argos_translation = translator.translate(line.text)
        for word in line.words:
            word.tr_definitions = lookup.lookup(word.lemma, grammar=word.grammar)
        report.lines.append(line)

    lookup.close()
    if translator is not None:
        translator.close()

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
    print(f"  Pipeline           : {report.pipeline}")
    print(f"  Line translation   : {'included' if any(l.argos_translation for l in report.lines) else 'not available'}")
    print(_SEP)


# ──────────────────────────────────────────────────────────────────────────────
# Entry point
# ──────────────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate word definitions + line translation for a language pair.")
    parser.add_argument("--pipeline", required=True, help="Pipeline path, e.g. ru-tr/kaikki_1")
    parser.add_argument("--api", default="https://singoling.com", help="Base API URL")
    parser.add_argument("--src", default="ru", help="Source language code (default: ru)")
    parser.add_argument("--tgt", default="tr", help="Target language code (default: tr)")
    parser.add_argument("--song-id", type=int, default=None, help="Specific song ID (default: random)")
    parser.add_argument("-n", type=int, default=10, help="Number of lines to sample (default: 10)")
    args = parser.parse_args()

    report = evaluate(args.api, args.src, args.tgt, args.n, args.song_id, args.pipeline)
    print_report(report)


if __name__ == "__main__":
    main()
