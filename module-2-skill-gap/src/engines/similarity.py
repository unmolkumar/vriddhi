"""Semantic similarity between skill names: MiniLM embeddings, TF-IDF character n-grams as fallback."""
from __future__ import annotations

import logging
import os
from functools import lru_cache

log = logging.getLogger(__name__)

MODEL_NAME = "all-MiniLM-L6-v2"
SEMANTIC_THRESHOLD = 0.82        # MiniLM cosine to call two skills adjacent (HANDOVER.md §3.3)
SEMANTIC_MATCH_THRESHOLD = 0.92  # ... and to treat them as the same skill (near-identical wording)
TFIDF_THRESHOLD = 0.82           # same bars for the fallback, on char 3-gram TF-IDF cosine
TFIDF_MATCH_THRESHOLD = 0.92
DISABLE_ENV = "M2_DISABLE_EMBEDDINGS"  # set to 1 to force the TF-IDF fallback


@lru_cache(maxsize=1)
def _model():
    """The MiniLM model, or None if it can't load (no network on first run, no torch, ...)."""
    if os.getenv(DISABLE_ENV) == "1":
        return None
    try:
        from sentence_transformers import SentenceTransformer

        return SentenceTransformer(MODEL_NAME)
    except Exception as e:
        log.warning("MiniLM unavailable, using TF-IDF fallback: %s", type(e).__name__)
        return None


def backend() -> str:
    return "minilm" if _model() is not None else "tfidf"


def threshold() -> float:
    """Similarity needed for 'adjacent'."""
    return SEMANTIC_THRESHOLD if backend() == "minilm" else TFIDF_THRESHOLD


def match_threshold() -> float:
    """Similarity needed for 'matched' (the same skill worded differently)."""
    return SEMANTIC_MATCH_THRESHOLD if backend() == "minilm" else TFIDF_MATCH_THRESHOLD


def similarity_matrix(left: list[str], right: list[str]) -> list[list[float]]:
    """Cosine similarity of every left name against every right name."""
    if not left or not right:
        return [[] for _ in left]
    model = _model()
    if model is not None:
        a = model.encode(left, normalize_embeddings=True)
        b = model.encode(right, normalize_embeddings=True)
        return (a @ b.T).tolist()
    from sklearn.feature_extraction.text import TfidfVectorizer
    from sklearn.metrics.pairwise import cosine_similarity

    vec = TfidfVectorizer(analyzer="char_wb", ngram_range=(3, 3), lowercase=True).fit(left + right)
    return cosine_similarity(vec.transform(left), vec.transform(right)).tolist()
