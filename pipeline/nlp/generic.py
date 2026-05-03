"""Generic passthrough backend for languages without dedicated NLP tooling."""

from __future__ import annotations

import re

from .base import NLPBackend, WordAnalysis


class GenericBackend(NLPBackend):
    """
    Zero-dependency backend that preserves original token forms verbatim.

    Suitable for any language where specialised morphological tooling is not
    yet integrated.  The `grammar` field is marked as unavailable so the UI
    can render a neutral placeholder rather than incorrect data.

    To add proper support for a language: create a new backend that implements
    `NLPBackend` and register it in the LANGUAGES dict in generate_song_data.py.
    """

    def load(self) -> None:
        pass  # no models to load

    def annotate_line(self, text: str) -> str | None:
        return None  # no phonetic annotation available

    def analyze_token(self, raw_token: str, annotated_token: str) -> WordAnalysis:
        clean = re.sub(r"[^\w]", "", raw_token, flags=re.UNICODE)
        return WordAnalysis(
            display_form=annotated_token,
            lemma=clean.lower() if clean else raw_token.lower(),
            grammar="(morphological analysis not yet available for this language)",
        )
