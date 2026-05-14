"""
Offline full-line translation via argostranslate.

Argos only provides translations to/from English as a pivot language.
When a direct src→tgt package doesn't exist, this module automatically
uses src→en→tgt as a two-step pipeline.

Usage:
    from eval.argos_translator import ArgosTranslator
    t = ArgosTranslator("ru", "tr")
    t.translate("Жил был художник один")
    # → "Bir ressam vardı"
"""

from __future__ import annotations

from typing import Optional

import argostranslate.package
import argostranslate.translate

_PIVOT = "en"


def _find_translation(installed, src: str, tgt: str):
    src_lang = next((l for l in installed if l.code == src), None)
    tgt_lang = next((l for l in installed if l.code == tgt), None)
    if src_lang is None or tgt_lang is None:
        return None
    return src_lang.get_translation(tgt_lang)


def _ensure_installed(src: str, tgt: str) -> None:
    """Raise a helpful error listing what's missing."""
    installed_codes = {l.code for l in argostranslate.translate.get_installed_languages()}
    missing = []
    if src not in installed_codes:
        missing.append(f"{src}→{_PIVOT}")
    if tgt not in installed_codes:
        missing.append(f"{_PIVOT}→{tgt}")
    if missing:
        pairs = "  and  ".join(missing)
        raise RuntimeError(
            f"Argos packages missing: {pairs}.\n"
            f"Run:  python -m eval.install_argos_pack {src} {_PIVOT}\n"
            f"      python -m eval.install_argos_pack {_PIVOT} {tgt}"
        )


class ArgosTranslator:
    """
    Wraps argostranslate for a fixed src→tgt pair.

    If no direct package exists, falls back to src→en→tgt pivot.
    """

    def __init__(self, src: str, tgt: str) -> None:
        self.src = src
        self.tgt = tgt
        installed = argostranslate.translate.get_installed_languages()

        # Try direct first
        direct = _find_translation(installed, src, tgt)
        if direct is not None:
            self._step1 = direct
            self._step2 = None
            self.mode = "direct"
        else:
            # Pivot via English
            _ensure_installed(src, tgt)
            step1 = _find_translation(installed, src, _PIVOT)
            step2 = _find_translation(installed, _PIVOT, tgt)
            if step1 is None or step2 is None:
                raise RuntimeError(
                    f"Could not build a translation path {src}→{tgt}. "
                    f"Run:  python -m eval.install_argos_pack {src} {_PIVOT}\n"
                    f"      python -m eval.install_argos_pack {_PIVOT} {tgt}"
                )
            self._step1 = step1
            self._step2 = step2
            self.mode = f"pivot ({src}→{_PIVOT}→{tgt})"

        print(f"  Argos translation mode: {self.mode}")

    # ------------------------------------------------------------------
    def translate(self, text: str) -> str:
        """Translate a single string and return the result."""
        if not text or not text.strip():
            return ""
        intermediate = self._step1.translate(text)
        if self._step2 is None:
            return intermediate
        return self._step2.translate(intermediate)
