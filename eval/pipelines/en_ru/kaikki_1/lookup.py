"""
en→ru lookup with spaCy-powered in-context lemmatization and POS disambiguation.

Improvements over a plain DB wrapper:
  1. Full-line spaCy (en_core_web_sm) for context-aware lemmatization / POS.
     "saw" in "I saw a shimmering light" → lemma "see", not "saw" (the tool).
  2. Preprocessing:
     - Contraction expansion:  livin' → living,  haven't → have not
     - Possessive stripping:   master's → master
     - Hyphenated-compound fallback: tiffany-twisted → try each part
     - Leading/trailing punctuation stripped before lookup
  3. Noise filtering:
     - DET tokens (a / an / the) skipped (no Russian article equivalent)
     - Single-character tokens skipped except "I" (pronoun → я)
     - Tokens containing non-ASCII non-apostrophe characters skipped

Requires:
    python -m spacy download en_core_web_sm
    python -m eval.pipelines.en_ru.kaikki_1.build_db

Run eval:
    python -m eval.run --pipeline en_ru/kaikki_1 --src en --tgt ru --song-id 105
"""

from __future__ import annotations

import re
import sqlite3
from collections import deque
from pathlib import Path

from .overrides import EN_RU_OVERRIDES

_HERE = Path(__file__).parent
_REPO_ROOT = _HERE.parents[3]   # eval/pipelines/en_ru/kaikki_1 → repo root

DEFAULT_DB = _HERE / "data" / "en_ru.db"
_PROD_DB = _REPO_ROOT / "backend" / "dictionaries" / "en_ru" / "en_ru.db"

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
]
for _surface, _expanded in _RAW_CONTRACTIONS:
    _tokens = _expanded.split()
    for _variant in (_surface, _surface.replace("'", "\u2019")):
        _CONTRACTIONS[_variant] = _tokens

# ---------------------------------------------------------------------------
# Auxiliary / function words that are the secondary part of contractions
# (not worth looking up on their own)
# ---------------------------------------------------------------------------
_AUX_WORDS: frozenset[str] = frozenset({
    "not", "am", "is", "are", "was", "were",
    "have", "has", "had", "will", "would",
    "can", "could", "should", "shall", "may", "might",
    "do", "did", "be", "been", "us",
})

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

# spaCy POS tags to skip entirely (no useful Russian equivalent)
_SKIP_POS: frozenset[str] = frozenset({"DET", "PUNCT", "SPACE", "SYM", "X", "NUM"})

# ---------------------------------------------------------------------------
# Regex helpers
# ---------------------------------------------------------------------------
_STRIP_RE     = re.compile(r"^[^\w']+|[^\w']+$")
_POSSESSIVE_RE = re.compile(r"\u2019?s$|'\s*s$", re.IGNORECASE)
_HYPHEN_RE    = re.compile(r"-")
_ASCII_WORD_RE = re.compile(r"^[a-zA-Z']+$")


def _find_db() -> Path:
    for p in (DEFAULT_DB, _PROD_DB):
        if p.exists():
            return p
    raise FileNotFoundError(
        "en_ru.db not found.\n"
        "Build it first:  python -m eval.pipelines.en_ru.kaikki_1.build_db"
    )


def _strip(token: str) -> str:
    """Strip leading/trailing non-word, non-apostrophe characters."""
    return _STRIP_RE.sub("", token)


def _normalise_apostrophe(s: str) -> str:
    """Replace curly apostrophes with straight ones for table lookup."""
    return s.replace("\u2019", "'").replace("\u2018", "'")


def _is_noise(clean: str, spacy_pos: str | None) -> bool:
    """True if token should be skipped without DB lookup."""
    if not clean:
        return True
    if spacy_pos in _SKIP_POS:
        return True
    if not _ASCII_WORD_RE.match(clean):
        # Contains non-ASCII or digits — mixed script / numeric
        return True
    if len(clean) == 1 and clean.lower() != "i":
        # Single character that isn't the English pronoun I
        return True
    return False


# ---------------------------------------------------------------------------
# Lookup class
# ---------------------------------------------------------------------------

class Lookup:
    """
    en→ru lookup combining spaCy in-context lemmatization with the kaikki DB.

    The eval harness calls ``lookup_line(line_text, words)`` (line-level,
    context-aware) when available; ``lookup(lemma, grammar)`` is the
    single-word fallback used by pipelines that lack lookup_line support.
    """

    def __init__(self, src: str, tgt: str) -> None:
        db_path = _find_db()
        self._conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        self._conn.row_factory = sqlite3.Row

        try:
            import spacy  # type: ignore[import-untyped]
            self._nlp = spacy.load("en_core_web_sm")
        except (ImportError, OSError) as exc:
            print(f"[en_ru lookup] spaCy unavailable ({exc}); falling back to DB-only lookup.")
            self._nlp = None

    # ------------------------------------------------------------------
    # DB access
    # ------------------------------------------------------------------

    def _db_lookup(self, lemma: str, pos: str | None) -> list[str]:
        """Return up to _MAX_RESULTS Russian translations, POS-preferred."""
        seen: set[str] = set()

        def _dedup(rows) -> list[str]:
            out: list[str] = []
            for r in rows:
                w = r["ru_word"].strip()
                if w and w not in seen:
                    seen.add(w)
                    out.append(w)
            return out

        if pos:
            rows = self._conn.execute(
                "SELECT DISTINCT ru_word FROM definitions WHERE lemma = ? AND pos = ?",
                (lemma, pos),
            ).fetchall()
            result = _dedup(rows)
            if result:
                return result[:_MAX_RESULTS]

        rows = self._conn.execute(
            "SELECT DISTINCT ru_word FROM definitions WHERE lemma = ?",
            (lemma,),
        ).fetchall()
        return _dedup(rows)[:_MAX_RESULTS]

    # ------------------------------------------------------------------
    # Single-token preprocessing + lookup (no line context)
    # ------------------------------------------------------------------

    def _lookup_token(self, token: str, pos: str | None = None) -> list[str]:
        """Preprocess one token and query the DB.  No context; pos may be None."""
        clean = _strip(_normalise_apostrophe(token))
        if not clean:
            return []

        # Strip possessive
        without_poss = _POSSESSIVE_RE.sub("", clean).strip("'")
        if without_poss and without_poss != clean:
            clean = without_poss

        # Direct lookup
        result = self._db_lookup(clean.lower(), pos)
        if result:
            return result

        # Hyphenated compound fallback: try each part
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
        Process the full lyric line with spaCy for context-aware POS tagging
        and lemmatization, then populate ``word.tr_definitions`` for each word.

        Falls back to word-level spaCy (no context) when a token can't be
        aligned (e.g. contractions split by spaCy differently).
        """
        if self._nlp is None:
            for word in words:
                word.tr_definitions = self.lookup(word.lemma)
            return

        # Run spaCy on the original line text
        doc = self._nlp(line_text)

        # Build pool: stripped_lower → deque[(spacy_lemma, kaikki_pos)]
        # Uses a deque so repeated surface forms are consumed left-to-right.
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
                # Expanded is a list of tokens; find the first non-aux content word
                result: list[str] = []
                for part in expanded:
                    if part in _AUX_WORDS:
                        continue
                    # Try from spaCy pool (if the expanded token appeared in line)
                    if part in pool and pool[part]:
                        spacy_lemma, kaikki_pos, spacy_pos_raw = pool[part][0]  # peek, don't pop
                        if not _is_noise(part, spacy_pos_raw):
                            result = self._db_lookup(spacy_lemma, kaikki_pos)
                    if not result:
                        # Fallback: run spaCy on the single expanded word
                        result = self._spacy_single(part, None)
                    if result:
                        break
                word.tr_definitions = result
                continue

            # ── 2. Noise filter (before pool lookup so DET etc. are caught) ─
            # Get pos hint from pool without consuming
            pool_entry = pool.get(clean)
            pool_pos = pool_entry[0][2] if pool_entry else None  # type: ignore[index]
            if _is_noise(clean, pool_pos):
                word.tr_definitions = []
                continue

            # ── 2b. Override by surface form ─────────────────────────────────
            ov = EN_RU_OVERRIDES.get(clean)
            if ov is not None:
                word.tr_definitions = ov
                continue

            # ── 3. Pool lookup (in-context) ─────────────────────────────────
            if pool_entry:
                spacy_lemma, kaikki_pos, spacy_pos_raw = pool_entry.popleft()  # type: ignore[misc]
                # Strip possessive from spaCy lemma too
                spacy_lemma = _POSSESSIVE_RE.sub("", spacy_lemma).strip("'") or spacy_lemma

                # Override by spaCy lemma (catches inflected forms: was→be, had→have)
                ov = EN_RU_OVERRIDES.get(spacy_lemma)
                if ov is not None:
                    word.tr_definitions = ov
                    continue

                word.tr_definitions = self._db_lookup(spacy_lemma, kaikki_pos) or \
                                      self._lookup_token(clean, kaikki_pos)
                continue

            # ── 4. Not found in pool — fall back to single-token spaCy ──────
            word.tr_definitions = self._spacy_single(clean, None)

    def _spacy_single(self, token: str, pos_hint: str | None) -> list[str]:
        """Run spaCy on a single token (no line context) and do DB lookup."""
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
        ov = EN_RU_OVERRIDES.get(lemma)
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
        """Single-word lookup (no line context). Used as fallback by eval harness."""
        norm = _normalise_apostrophe(lemma)
        clean = _strip(norm)

        # Contraction
        expanded = _CONTRACTIONS.get(norm.lower()) or _CONTRACTIONS.get(clean.lower())
        if expanded:
            for part in expanded:
                if part not in _AUX_WORDS:
                    ov = EN_RU_OVERRIDES.get(part)
                    if ov is not None:
                        return ov
                    result = self._spacy_single(part, None)
                    if result:
                        return result
            return []

        # Override by surface form
        ov = EN_RU_OVERRIDES.get(clean.lower())
        if ov is not None:
            return ov

        return self._spacy_single(clean, None)

    def close(self) -> None:
        self._conn.close()
