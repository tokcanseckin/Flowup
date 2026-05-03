"""
pymorphy3-based backend for Russian and Ukrainian.

Optional ruaccent integration adds contextual stress marks for Russian.
Both libraries are imported lazily inside `load()` so the script stays
importable even if they are not installed.
"""

from __future__ import annotations

import re

from .base import NLPBackend, WordAnalysis

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
    "accs": "Accusative", "ablt": "Ablative",  "loct": "Locative",
    "voct": "Vocative",   "gen2": "Genitive 2",
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

    def _accent_text(self, text: str) -> str:
        """Apply stress marks with compatibility across ruaccent versions."""
        if self._accent is None:
            return text

        # Newer ruaccent builds expose process_all(), older ones process_text().
        if hasattr(self._accent, "process_text"):
            return self._accent.process_text(text)

        if hasattr(self._accent, "process_all"):
            out = self._accent.process_all(text)
            if isinstance(out, list):
                return out[0] if out else text
            return out

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
            # Try to get stress for lemma from RUAccent applied to lemma form itself.
            # RUAccent is context-sensitive, so applying to the full line (annotated_token)
            # gives better results. We prefer lemma with stress if possible.
            lemma_display = self._accent_text(lemma) if self._accent else lemma
            grammar = _humanize(best)
        else:
            lemma = clean
            lemma_display = self._accent_text(lemma) if self._accent else lemma
            grammar = "Unknown"

        return WordAnalysis(
            display_form=annotated_token,  # Declined form WITH stress from full-line RUAccent processing
            lemma=lemma_display,            # Nominative form WITH stress applied separately
            grammar=grammar,
        )
