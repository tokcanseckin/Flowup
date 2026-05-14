from __future__ import annotations
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parent))
from argos_translator import ArgosTranslator as _ArgosTranslator


class Translator:
    def __init__(self, src: str, tgt: str) -> None:
        print(f"Loading Argos {src}→{tgt} model …")
        self._inner = _ArgosTranslator(src, tgt)

    def translate(self, text: str) -> str:
        return self._inner.translate(text)

    def close(self) -> None:
        pass
