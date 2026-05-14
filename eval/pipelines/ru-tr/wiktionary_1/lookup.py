from __future__ import annotations
from pathlib import Path
import sys

# Make the wiktionary module importable from this folder
sys.path.insert(0, str(Path(__file__).parent))
from wiktionary import WiktionaryLookup as _WiktionaryLookup


class Lookup:
    def __init__(self, src: str, tgt: str) -> None:
        self._inner = _WiktionaryLookup()

    def lookup(self, lemma: str, grammar: str = "") -> list[str]:
        return self._inner.lookup(lemma, grammar=grammar)

    def close(self) -> None:
        self._inner.close()
