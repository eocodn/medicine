"""Contracts, datasets, and evaluation tools for learned medication parsing."""

from .contract import Corpus, CorpusCase, CorpusError, OcrBox, load_corpus
from .evaluation import evaluate_case

__all__ = [
    "Corpus",
    "CorpusCase",
    "CorpusError",
    "OcrBox",
    "evaluate_case",
    "load_corpus",
]
