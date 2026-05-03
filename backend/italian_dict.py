"""
Italian word lookup using NLTK Open Multilingual Wordnet (offline).

Provides English definitions for Italian lemmas by mapping them to
Princeton WordNet synsets via the Italian OMW-1.4 data.

One-time setup (done automatically on first use):
    python -c "import italian_dict; italian_dict.ensure_loaded()"

Requirements:
    pip install nltk
    # NLTK data is downloaded automatically on first call to ensure_loaded()
"""

from __future__ import annotations

from typing import Optional

_loaded: bool = False
_wn = None  # nltk.corpus.wordnet, set after ensure_loaded()


def ensure_loaded() -> None:
    """Download NLTK data if needed and initialise the wordnet interface."""
    global _loaded, _wn
    if _loaded:
        return
    try:
        import nltk
        nltk.download("wordnet", quiet=True)
        nltk.download("omw-1.4", quiet=True)
        from nltk.corpus import wordnet
        _wn = wordnet
        _loaded = True
        print("[Dictionary] Italian OMW loaded.")
    except Exception as exc:
        print(f"[Dictionary] Italian OMW unavailable: {exc}")
        _loaded = True  # mark as attempted so we don't retry


def lookup_all(lemma: str) -> list[str]:
    """
    Return all English definitions for an Italian lemma (one per synset).

    Looks up Italian synsets in OMW-1.4 and collects definitions from each
    matching Princeton WordNet synset (definitions are in English).
    """
    if _wn is None:
        return []
    results: list[str] = []
    try:
        synsets = _wn.synsets(lemma.lower(), lang="ita")
        for syn in synsets:
            defn = syn.definition()
            if defn and defn not in results:
                results.append(defn)
    except Exception:
        pass
    return results


def lookup(lemma: str) -> Optional[str]:
    """
    Return an English definition for an Italian lemma, or None if not found.

    Looks up Italian synsets in OMW-1.4 and returns the definition of the
    first matching Princeton WordNet synset (definitions are in English).
    """
    defs = lookup_all(lemma)
    return defs[0] if defs else None
