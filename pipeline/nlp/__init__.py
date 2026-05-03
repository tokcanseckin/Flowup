from .base import NLPBackend, WordAnalysis
from .generic import GenericBackend
from .pymorphy import PyMorphyBackend
from .spacy_backend import SpaCyBackend

__all__ = ["NLPBackend", "WordAnalysis", "GenericBackend", "PyMorphyBackend", "SpaCyBackend"]
