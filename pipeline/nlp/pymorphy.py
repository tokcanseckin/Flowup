"""
pymorphy3-based backend for Russian and Ukrainian.

Optional ruaccent integration adds contextual stress marks for Russian.
Both libraries are imported lazily inside `load()` so the script stays
importable even if they are not installed.
"""

from __future__ import annotations

import re

from .base import NLPBackend, WordAnalysis


_VOWELS = "АЕЁИОУЫЭЮЯаеёиоуыэюя"


def _normalize_stress_marks(text: str) -> str:
    """Convert ruaccent's plus-before-vowel markers into combining acute accents.

    Also strips any redundant combining acute that lands on ё/Ё, which are
    inherently stressed in Russian and need no additional mark.
    """
    result = re.sub(rf"\+([{_VOWELS}])", lambda m: m.group(1) + "\u0301", text)
    # Remove U+0301 immediately after ё or Ё (already stressed by nature).
    result = re.sub(r"([ёЁ])\u0301", r"\1", result)
    return result

# ── Grammar tag maps (pymorphy3 internal codes → human labels) ────────────────

_POS = {
    "NOUN": "Noun",         "ADJF": "Adjective",          "ADJS": "Adj (short)",
    "COMP": "Comparative",  "VERB": "Verb",                "INFN": "Verb (infinitive)",
    "PRTF": "Participle",   "PRTS": "Participle (short)",  "GRND": "Gerund",
    "NUMR": "Numeral",      "ADVB": "Adverb",              "NPRO": "Pronoun",
    "PRED": "Predicative",  "PREP": "Preposition",         "CONJ": "Conjunction",
    "PRCL": "Particle",     "INTJ": "Interjection",
}
_CASE = {
    "nomn": "Nominative", "gent": "Genitive",  "datv": "Dative",
    "accs": "Accusative", "ablt": "Instrumental", "loct": "Prepositional",
    "voct": "Vocative",   "gen2": "Genitive 2", "loc2": "Prepositional",
}
_GEND = {"masc": "Masculine", "femn": "Feminine",  "neut": "Neuter"}
_NUM  = {"sing": "Singular",  "plur": "Plural"}
_TENS = {"pres": "Present",   "past": "Past",       "futr": "Future"}
_PERS = {"1per": "1st Person","2per": "2nd Person", "3per": "3rd Person"}


def _humanize(parse) -> str:
    t = parse.tag
    parts: list[str] = []
    if t.POS:    parts.append(_POS.get(t.POS, t.POS))
    if t.tense:  parts.append(_TENS.get(t.tense, t.tense))
    if t.person: parts.append(_PERS.get(t.person, t.person))
    if t.number: parts.append(_NUM.get(t.number, t.number))
    if t.gender: parts.append(_GEND.get(t.gender, t.gender))
    if t.case:   parts.append(_CASE.get(t.case, t.case))
    return ", ".join(parts) or "Unknown"


# ── Backend ───────────────────────────────────────────────────────────────────

class PyMorphyBackend(NLPBackend):
    """
    Morphological analysis via pymorphy3 with optional ruaccent stress marks.

    Args:
        morph_lang:  pymorphy3 dictionary language — "ru" (default) or "uk".
        use_accent:  if True, load ruaccent for Russian stress annotation.
                     Only meaningful when morph_lang == "ru".
    """

    def __init__(self, morph_lang: str = "ru", use_accent: bool = True) -> None:
        self._morph_lang = morph_lang
        self._use_accent = use_accent
        self._morph  = None
        self._accent = None

    def load(self) -> None:
        import pymorphy3
        self._morph = pymorphy3.MorphAnalyzer(lang=self._morph_lang)
        print(f"       pymorphy3 ({self._morph_lang}) ready.")

        if self._use_accent:
            from ruaccent import RUAccent
            self._accent = RUAccent()
            self._accent.load(omograph_model_size="turbo", use_dictionary=True)
            print("       ruaccent ready.")

    def _run_accent(self, text: str) -> str:
        """Call the underlying ruaccent API and return the raw (un-normalised) result."""
        if hasattr(self._accent, "process_text"):
            return self._accent.process_text(text)
        if hasattr(self._accent, "process_all"):
            out = self._accent.process_all(text)
            if isinstance(out, list):
                return out[0] if out else text
            return out
        return text

    def _accent_text(self, text: str) -> str:
        """Apply stress marks with compatibility across ruaccent versions.

        ruaccent is context-sensitive and intentionally leaves some common
        function words (e.g. 'только', 'тускло') without stress marks when
        they appear mid-sentence.  We fix this with a per-word retry: any
        space-separated token that still contains no stress marker after
        full-line processing is retried in isolation so that single-word
        context triggers the dictionary lookup.
        """
        if self._accent is None:
            return text

        try:
            annotated = _normalize_stress_marks(self._run_accent(text))

            # Per-word retry for tokens that received no stress mark.
            orig_tokens  = text.split()
            annot_tokens = annotated.split()

            if len(orig_tokens) != len(annot_tokens):
                # Token count mismatch — return as-is to avoid misalignment.
                return annotated

            fixed: list[str] = []
            for orig, annot in zip(orig_tokens, annot_tokens):
                # Check whether this token already carries a combining acute (U+0301).
                if "\u0301" not in annot and sum(1 for c in orig if c in _VOWELS) > 1:
                    # Retry the word in isolation.
                    solo = _normalize_stress_marks(self._run_accent(orig))
                    fixed.append(solo if "\u0301" in solo else annot)
                else:
                    fixed.append(annot)

            return " ".join(fixed)
        except Exception:
            pass  # fall through to raw text on any ruaccent/ONNX failure

        return text

    def annotate_line(self, text: str) -> str | None:
        if self._accent is None:
            return None
        return self._accent_text(text)

    def analyze_token(self, raw_token: str, annotated_token: str) -> WordAnalysis:
        clean = re.sub(r"[^\w]", "", raw_token, flags=re.UNICODE)
        if not clean:
            return WordAnalysis(display_form=annotated_token, lemma=raw_token, grammar="Unknown")

        parses = self._morph.parse(clean)  # type: ignore[union-attr]
        best   = parses[0] if parses else None

        if best:
            lemma  = best.normal_form
            lemma_display = self._accent_text(lemma) if self._accent else lemma

            # Collect all distinct grammatical readings, preserving probability order.
            seen: dict[str, None] = {}
            for p in parses:
                h = _humanize(p)
                if h and h != "Unknown":
                    seen[h] = None
            grammar = " / ".join(seen) if seen else "Unknown"
        else:
            lemma = clean
            lemma_display = self._accent_text(lemma) if self._accent else lemma
            grammar = "Unknown"

        return WordAnalysis(
            display_form=annotated_token,  # Declined form WITH stress from full-line RUAccent processing
            lemma=lemma_display,            # Nominative form WITH stress applied separately
            grammar=grammar,
        )
