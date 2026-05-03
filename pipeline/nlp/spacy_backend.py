"""
spaCy-based NLP backend for languages with official spaCy models.

Supported language codes and their model names:
  de → de_core_news_sm   (German)
  es → es_core_news_sm   (Spanish)
  fr → fr_core_news_sm   (French)
  it → it_core_news_sm   (Italian)
  nl → nl_core_news_sm   (Dutch)
  pl → pl_core_news_sm   (Polish)
  pt → pt_core_news_sm   (Portuguese)
  ja → ja_core_news_sm   (Japanese)
  zh → zh_core_web_sm    (Chinese)
  ko → ko_core_news_sm   (Korean)

Install a model before running the pipeline:
  python -m spacy download de_core_news_sm

If a model is not installed the backend degrades gracefully to the
same output as GenericBackend (lemma = lowercase token, no grammar).
"""

from __future__ import annotations

import re
from typing import Optional

from .base import NLPBackend, WordAnalysis

_MODEL_MAP: dict[str, str] = {
    "de": "de_core_news_sm",
    "es": "es_core_news_sm",
    "fr": "fr_core_news_sm",
    "it": "it_core_news_sm",
    "nl": "nl_core_news_sm",
    "pl": "pl_core_news_sm",
    "pt": "pt_core_news_sm",
    "ja": "ja_core_news_sm",
    "zh": "zh_core_web_sm",
    "ko": "ko_core_news_sm",
}

# Universal Dependencies POS → human labels
_POS_LABELS: dict[str, str] = {
    "NOUN":  "Noun",         "VERB":  "Verb",         "AUX":   "Auxiliary Verb",
    "ADJ":   "Adjective",    "ADV":   "Adverb",       "PRON":  "Pronoun",
    "DET":   "Determiner",   "ADP":   "Preposition",  "CCONJ": "Conjunction",
    "SCONJ": "Conjunction",  "PART":  "Particle",     "NUM":   "Numeral",
    "PROPN": "Proper Noun",  "INTJ":  "Interjection", "PUNCT": "Punctuation",
    "SYM":   "Symbol",       "X":     "Other",
}

# Morphological feature values → human labels (ordered priority list)
_FEATURE_ORDER = [
    "Number", "Gender", "Case", "Tense", "Aspect",
    "Mood", "Voice", "Person", "Degree", "Definite",
]
_MORPH_LABELS: dict[str, dict[str, str]] = {
    "Number":   {"Sing": "Singular",      "Plur": "Plural",         "Ptan": "Plural-only"},
    "Gender":   {"Masc": "Masculine",     "Fem":  "Feminine",       "Neut": "Neuter",   "Com": "Common"},
    "Case":     {"Nom":  "Nominative",    "Gen":  "Genitive",       "Dat":  "Dative",
                 "Acc":  "Accusative",    "Ins":  "Instrumental",   "Loc":  "Locative",
                 "Voc":  "Vocative",      "Abl":  "Ablative"},
    "Tense":    {"Past": "Past",          "Pres": "Present",        "Fut":  "Future",   "Imp": "Imperfect"},
    "Aspect":   {"Imp":  "Imperfective",  "Perf": "Perfective"},
    "Mood":     {"Ind":  "Indicative",    "Imp":  "Imperative",     "Cnd":  "Conditional", "Sub": "Subjunctive"},
    "Voice":    {"Act":  "Active",        "Pass": "Passive"},
    "Person":   {"1":    "1st Person",    "2":    "2nd Person",     "3":    "3rd Person"},
    "Degree":   {"Pos":  "Positive",      "Cmp":  "Comparative",   "Sup":  "Superlative"},
    "Definite": {"Def":  "Definite",      "Ind":  "Indefinite"},
}


def _humanize_morph(morph_dict: dict[str, str]) -> list[str]:
    parts: list[str] = []
    for feat in _FEATURE_ORDER:
        val = morph_dict.get(feat)
        if val:
            label = _MORPH_LABELS.get(feat, {}).get(val, val)
            parts.append(label)
    return parts


class SpaCyBackend(NLPBackend):
    """
    Morphological analysis via spaCy.

    Args:
        lang_code: ISO 639-1 code — must be a key in _MODEL_MAP.
    """

    def __init__(self, lang_code: str) -> None:
        if lang_code not in _MODEL_MAP:
            raise ValueError(
                f"No spaCy model registered for '{lang_code}'. "
                f"Supported: {sorted(_MODEL_MAP)}"
            )
        self._lang_code  = lang_code
        self._model_name = _MODEL_MAP[lang_code]
        self._nlp        = None

    def load(self) -> None:
        try:
            import spacy  # type: ignore[import-untyped]
            self._nlp = spacy.load(self._model_name)
            print(f"       spaCy ({self._model_name}) ready.")
        except OSError:
            print(f"       [WARNING] spaCy model '{self._model_name}' not installed.")
            print(f"       Run: python -m spacy download {self._model_name}")
            print(f"       Falling back to generic (no morphology).")
            self._nlp = None

    def annotate_line(self, text: str) -> Optional[str]:
        # spaCy does not produce phonetic annotations
        return None

    def analyze_token(self, raw_token: str, annotated_token: str) -> WordAnalysis:
        clean = re.sub(r"[^\w]", "", raw_token, flags=re.UNICODE)
        if not clean or self._nlp is None:
            return WordAnalysis(
                display_form=annotated_token,
                lemma=clean.lower() if clean else raw_token.lower(),
                grammar="(morphological analysis not available for this language)" if self._nlp is None
                        else "Unknown",
            )

        doc   = self._nlp(clean)
        token = doc[0] if doc else None

        if token is None:
            return WordAnalysis(
                display_form=annotated_token,
                lemma=clean.lower(),
                grammar="Unknown",
            )

        pos_label   = _POS_LABELS.get(token.pos_, token.pos_) if token.pos_ else ""
        morph_dict  = token.morph.to_dict() if hasattr(token.morph, "to_dict") else dict(token.morph)
        morph_parts = _humanize_morph(morph_dict)

        grammar = ", ".join(p for p in [pos_label] + morph_parts if p) or "Unknown"
        lemma   = token.lemma_ if token.lemma_ else clean.lower()

        return WordAnalysis(
            display_form=annotated_token,
            lemma=lemma,
            grammar=grammar,
        )
