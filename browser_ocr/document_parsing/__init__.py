"""Model-free research tools for medication-document structure parsing."""

from .baseline import BASELINE_ID, parse_boxes, run_baseline
from .contract import Corpus, CorpusCase, CorpusError, OcrBox, load_corpus
from .evaluation import evaluate_case

__all__ = [
    "BASELINE_ID",
    "Corpus",
    "CorpusCase",
    "CorpusError",
    "OcrBox",
    "evaluate_case",
    "load_corpus",
    "parse_boxes",
    "run_baseline",
]