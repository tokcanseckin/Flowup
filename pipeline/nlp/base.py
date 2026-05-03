"""Abstract base for language-specific NLP backends."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class WordAnalysis:
    """Language-agnostic result of analysing a single word token."""
    display_form: str   # annotated inflected form (stress, tone, diacritics…)
    lemma: str          # dictionary base form, possibly annotated
    grammar: str        # human-readable grammatical description


class NLPBackend(ABC):
    """
    Interface that every language backend must implement.

    Implementations may load heavyweight models lazily inside `load()`.
    The pipeline calls `load()` once before processing any lines.
    """

    @abstractmethod
    def load(self) -> None:
        """Initialise models / resources. Called exactly once per run."""

    @abstractmethod
    def annotate_line(self, text: str) -> str | None:
        """
        Add phonetic annotations to a complete line (context-aware).

        Returns the annotated string, or None if this language / backend
        does not produce phonetic annotations (e.g. most Latin-script
        languages where the orthography already encodes pronunciation).
        """

    @abstractmethod
    def analyze_token(self, raw_token: str, annotated_token: str) -> WordAnalysis:
        """
        Return morphological analysis for a single whitespace-split token.

        `raw_token`       – original text as it appears in the lyric line
        `annotated_token` – the same token after `annotate_line` processing
                            (equals `raw_token` when no annotation was done)
        """
