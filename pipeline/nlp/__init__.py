from .base import NLPBackend, WordAnalysis
from .generic import GenericBackend
from .pymorphy import PyMorphyBackend

__all__ = ["NLPBackend", "WordAnalysis", "GenericBackend", "PyMorphyBackend"]
