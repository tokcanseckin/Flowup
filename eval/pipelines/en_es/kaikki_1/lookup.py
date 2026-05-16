"""
en→es lookup with spaCy-powered in-context lemmatization and POS disambiguation.

Mirrors eval/pipelines/en_ru/kaikki_1/lookup.py but targets Spanish (es_word)
instead of Russian.

Requires:
    python -m spacy download en_core_web_sm
    python -m eval.pipelines.en_es.kaikki_1.build_db

Run eval:
    python -m eval.run --pipeline en_es/kaikki_1 --src en --tgt es --song-id 217
"""

from __future__ import annotations

import re
import sqlite3
from collections import deque
from pathlib import Path

from .overrides import EN_ES_OVERRIDES

_HERE = Path(__file__).parent
_REPO_ROOT = _HERE.parents[3]   # eval/pipelines/en_es/kaikki_1 → repo root

DEFAULT_DB = _HERE / "data" / "en_es.db"
_PROD_DB = _REPO_ROOT / "backend" / "dictionaries" / "en_es" / "en_es.db"

_MAX_RESULTS = 4

# ---------------------------------------------------------------------------
# Contraction table  (lowercase surface form, both straight and curly quotes)
# ---------------------------------------------------------------------------
_CONTRACTIONS: dict[str, list[str]] = {}
_RAW_CONTRACTIONS: list[tuple[str, str]] = [
    ("can't",    "cannot"),
    ("won't",    "will not"),
    ("don't",    "do not"),
    ("didn't",   "did not"),
    ("doesn't",  "does not"),
    ("haven't",  "have not"),
    ("hasn't",   "has not"),
    ("hadn't",   "had not"),
    ("isn't",    "is not"),
    ("aren't",   "are not"),
    ("wasn't",   "was not"),
    ("weren't",  "were not"),
    ("wouldn't", "would not"),
    ("couldn't", "could not"),
    ("shouldn't","should not"),
    ("mustn't",  "must not"),
    ("needn't",  "need not"),
    ("i'm",      "i am"),
    ("i've",     "i have"),
    ("i'll",     "i will"),
    ("i'd",      "i would"),
    ("you're",   "you are"),
    ("you've",   "you have"),
    ("you'll",   "you will"),
    ("you'd",    "you would"),
    ("he's",     "he is"),
    ("she's",    "she is"),
    ("it's",     "it is"),
    ("we're",    "we are"),
    ("we've",    "we have"),
    ("we'll",    "we will"),
    ("we'd",     "we would"),
    ("they're",  "they are"),
    ("they've",  "they have"),
    ("they'll",  "they will"),
    ("they'd",   "they would"),
    ("that's",   "that is"),
    ("what's",   "what is"),
    ("there's",  "there is"),
    ("here's",   "here is"),
    ("let's",    "let us"),
    ("ain't",    "am not"),
    # informal/elided forms
    ("livin'",   "living"),
    ("goin'",    "going"),
    ("comin'",   "coming"),
    ("somethin'","something"),
    ("nothin'",  "nothing"),
    ("everythin'","everything"),
    ("doin'",    "doing"),
    ("sayin'",   "saying"),
    ("tryin'",   "trying"),
    ("feelin'",  "feeling"),
    ("'em",      "them"),
    ("'cause",   "because"),
    ("'til",     "until"),
    # informal compressions
    ("wanna",    "want to"),
    ("gonna",    "going to"),
    ("gotta",    "got to"),
]
for _surface, _expanded in _RAW_CONTRACTIONS:
    _tokens = _expanded.split()
    for _variant in (_surface, _surface.replace("'", "\u2019")):
        _CONTRACTIONS[_variant] = _tokens

# ---------------------------------------------------------------------------
# Auxiliary / function words that are the secondary part of contractions
# ---------------------------------------------------------------------------
# Words skipped during contraction expansion UNLESS they have an override entry.
# "do"/"did" removed so "don't" → "hacer" works; "to" added to skip the infinitive
# marker in "wanna" / "gonna" / "gotta" expansions.
_AUX_WORDS: frozenset[str] = frozenset({
    "not", "am", "is", "are", "was", "were",
    "have", "has", "had", "will", "would",
    "can", "could", "should", "shall", "may", "might",
    "be", "been", "us", "to",
})

# Always silenced in contraction expansion even when the word has an override
# ("to" has a preposition override for standalone use but is a meaningless
# infinitive marker in "wanna"/"gonna"/"gotta" expansions).
_CONTRACTION_SILENT: frozenset[str] = frozenset({"to"})

# ---------------------------------------------------------------------------
# spaCy Universal Dependencies POS → kaikki DB pos values
# ---------------------------------------------------------------------------
_SPACY_TO_POS: dict[str, str] = {
    "NOUN":  "noun",
    "PROPN": "noun",
    "VERB":  "verb",
    "AUX":   "verb",
    "ADJ":   "adj",
    "ADV":   "adv",
    "PRON":  "pron",
    "NUM":   "num",
    "ADP":   "prep",
    "CCONJ": "conj",
    "SCONJ": "conj",
    "PART":  "particle",
    "INTJ":  "intj",
}

# spaCy POS tags to skip entirely
_SKIP_POS: frozenset[str] = frozenset({"DET", "PUNCT", "SPACE", "SYM", "X", "NUM"})

# ---------------------------------------------------------------------------
# Regex helpers
# ---------------------------------------------------------------------------
_STRIP_RE      = re.compile(r"^[^\w']+|[^\w']+$")
_POSSESSIVE_RE = re.compile(r"\u2019?s$|'\s*s$", re.IGNORECASE)
_HYPHEN_RE     = re.compile(r"-")
_ASCII_WORD_RE = re.compile(r"^[a-zA-Z']+$")


def _find_db() -> Path:
    for p in (DEFAULT_DB, _PROD_DB):
        if p.exists():
            return p
    raise FileNotFoundError(
        "en_es.db not found.\n"
        "Build it first:  python -m eval.pipelines.en_es.kaikki_1.build_db"
    )


def _strip(token: str) -> str:
    return _STRIP_RE.sub("", token)


def _normalise_apostrophe(s: str) -> str:
    return s.replace("\u2019", "'").replace("\u2018", "'")


def _is_noise(clean: str, spacy_pos: str | None) -> bool:
    if not clean:
        return True
    if spacy_pos in _SKIP_POS:
        return True
    if not _ASCII_WORD_RE.match(clean):
        return True
    if len(clean) == 1 and clean.lower() != "i":
        return True
    return False


# ---------------------------------------------------------------------------
# Lookup class
# ---------------------------------------------------------------------------

class Lookup:
    """
    en→es lookup combining spaCy in-context lemmatization with the kaikki DB.
    """

    def __init__(self, src: str, tgt: str) -> None:
        db_path = _find_db()
        self._conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        self._conn.row_factory = sqlite3.Row

        try:
            import spacy  # type: ignore[import-untyped]
            self._nlp = spacy.load("en_core_web_sm")
        except (ImportError, OSError) as exc:
            print(f"[en_es lookup] spaCy unavailable ({exc}); falling back to DB-only lookup.")
            self._nlp = None

    # ------------------------------------------------------------------
    # DB access
    # ------------------------------------------------------------------

    def _db_lookup(self, lemma: str, pos: str | None) -> list[str]:
        """Return up to _MAX_RESULTS Spanish translations, POS-preferred."""
        seen: set[str] = set()

        def _dedup(rows) -> list[str]:
            out: list[str] = []
            for r in rows:
                w = r["es_word"].strip()
                if w and w not in seen:
                    seen.add(w)
                    out.append(w)
            return out

        if pos:
            rows = self._conn.execute(
                "SELECT DISTINCT es_word FROM definitions WHERE lemma = ? AND pos = ?",
                (lemma, pos),
            ).fetchall()
            result = _dedup(rows)
            if result:
                return result[:_MAX_RESULTS]

        rows = self._conn.execute(
            "SELECT DISTINCT es_word FROM definitions WHERE lemma = ?",
            (lemma,),
        ).fetchall()
        return _dedup(rows)[:_MAX_RESULTS]

    # ------------------------------------------------------------------
    # Single-token preprocessing + lookup
    # ------------------------------------------------------------------

    def _lookup_token(self, token: str, pos: str | None = None) -> list[str]:
        clean = _strip(_normalise_apostrophe(token))
        if not clean:
            return []

        # Strip possessive
        without_poss = _POSSESSIVE_RE.sub("", clean).strip("'")
        if without_poss and without_poss != clean:
            clean = without_poss

        result = self._db_lookup(clean.lower(), pos)
        if result:
            return result

        # Hyphenated compound fallback
        parts = _HYPHEN_RE.split(clean)
        if len(parts) > 1:
            for part in parts:
                p = _strip(part)
                if p and _ASCII_WORD_RE.match(p) and len(p) > 1:
                    result = self._db_lookup(p.lower(), None)
                    if result:
                        return result

        return []

    # ------------------------------------------------------------------
    # Public: line-level (context-aware)
    # ------------------------------------------------------------------

    def lookup_line(self, line_text: str, words: list) -> None:
        """
        Process a full lyric line with spaCy for context-aware POS tagging and
        lemmatization, then populate ``word.tr_definitions`` for each word.
        """
        if self._nlp is None:
            for word in words:
                word.tr_definitions = self.lookup(word.lemma)
            return

        doc = self._nlp(line_text)

        pool: dict[str, deque[tuple[str, str | None]]] = {}
        for tok in doc:
            if tok.is_space:
                continue
            key = _strip(tok.text).lower()
            if key:
                kaikki_pos = _SPACY_TO_POS.get(tok.pos_)
                pool.setdefault(key, deque()).append(
                    (tok.lemma_.lower(), kaikki_pos, tok.pos_)  # type: ignore[arg-type]
                )

        for word in words:
            norm_display = _normalise_apostrophe(word.display_form)
            clean = _strip(norm_display).lower()

            # ── 1. Contraction check ────────────────────────────────────────
            expanded = _CONTRACTIONS.get(norm_display.lower()) or \
                       _CONTRACTIONS.get(clean)
            if expanded:
                # Collect glosses from ALL expanded tokens:
                #   - override entries win even for aux words (so "are" → "son/están",
                #     "would" → "habría", etc. are included alongside the pronoun)
                #   - _CONTRACTION_SILENT words (e.g. "to" in wanna/gonna) are always skipped
                #   - non-override aux words are still skipped
                all_results: list[str] = []
                for part in expanded:
                    if part in _CONTRACTION_SILENT:
                        continue
                    ov_part = EN_ES_OVERRIDES.get(part)
                    if ov_part is not None:
                        all_results.extend(ov_part)
                        continue
                    if part in _AUX_WORDS:
                        continue
                    part_result: list[str] = []
                    if part in pool and pool[part]:
                        spacy_lemma, kaikki_pos, spacy_pos_raw = pool[part][0]  # peek
                        if not _is_noise(part, spacy_pos_raw):
                            part_result = self._db_lookup(spacy_lemma, kaikki_pos)
                    if not part_result:
                        part_result = self._spacy_single(part, None)
                    all_results.extend(part_result)
                word.tr_definitions = all_results[:_MAX_RESULTS]
                continue

            # ── 2b. Override by surface form ─────────────────────────────────
            # Checked BEFORE noise filter so demonstratives/articles with empty
            # overrides ("this", "that", "a", "the") are handled correctly even
            # when spaCy tags them as DET (which _is_noise would suppress).
            ov = EN_ES_OVERRIDES.get(clean)
            if ov is not None:
                word.tr_definitions = ov
                continue

            # ── 2. Noise filter ─────────────────────────────────────────────
            pool_entry = pool.get(clean)
            pool_pos = pool_entry[0][2] if pool_entry else None  # type: ignore[index]
            if _is_noise(clean, pool_pos):
                word.tr_definitions = []
                continue

            # ── 3. Pool lookup (in-context) ─────────────────────────────────
            if pool_entry:
                spacy_lemma, kaikki_pos, spacy_pos_raw = pool_entry.popleft()  # type: ignore[misc]
                spacy_lemma = _POSSESSIVE_RE.sub("", spacy_lemma).strip("'") or spacy_lemma

                ov = EN_ES_OVERRIDES.get(spacy_lemma)
                if ov is not None:
                    word.tr_definitions = ov
                    continue

                word.tr_definitions = self._db_lookup(spacy_lemma, kaikki_pos) or \
                                      self._lookup_token(clean, kaikki_pos)
                continue

            # ── 4. Not found in pool — fall back to single-token spaCy ──────
            word.tr_definitions = self._spacy_single(clean, None)

    def _spacy_single(self, token: str, pos_hint: str | None) -> list[str]:
        if self._nlp is None:
            return self._lookup_token(token, pos_hint)
        doc = self._nlp(token)
        if not doc:
            return self._lookup_token(token, pos_hint)
        tok = doc[0]
        kaikki_pos = _SPACY_TO_POS.get(tok.pos_) or pos_hint
        spacy_pos_raw = tok.pos_
        lemma = tok.lemma_.lower()
        if _is_noise(lemma, spacy_pos_raw):
            return []
        ov = EN_ES_OVERRIDES.get(lemma)
        if ov is not None:
            return ov
        result = self._db_lookup(lemma, kaikki_pos)
        if not result:
            result = self._lookup_token(token, kaikki_pos)
        return result

    # ------------------------------------------------------------------
    # Public: single-word fallback
    # ------------------------------------------------------------------

    def lookup(self, lemma: str, grammar: str = "") -> list[str]:
        norm = _normalise_apostrophe(lemma)
        clean = _strip(norm)

        expanded = _CONTRACTIONS.get(norm.lower()) or _CONTRACTIONS.get(clean.lower())
        if expanded:
            all_results: list[str] = []
            for part in expanded:
                if part in _CONTRACTION_SILENT:
                    continue
                ov_part = EN_ES_OVERRIDES.get(part)
                if ov_part is not None:
                    all_results.extend(ov_part)
                    continue
                if part not in _AUX_WORDS:
                    part_result = self._spacy_single(part, None)
                    all_results.extend(part_result)
            return all_results[:_MAX_RESULTS]

        ov = EN_ES_OVERRIDES.get(clean.lower())
        if ov is not None:
            return ov

        return self._spacy_single(clean, None)

    def close(self) -> None:
        self._conn.close()
