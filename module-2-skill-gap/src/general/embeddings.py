"""Sentence embeddings for requirements and evidence: MiniLM by default, CPU only, cached on disk.

Requirement vectors are cached per (model, SOC, item-text hash) as float16 .npy under data/cache/embeddings
(gitignored), so a changed requirement list is re-encoded and an unchanged one never is.
"""
from __future__ import annotations

import hashlib
import logging
import os
import re
from functools import lru_cache
from pathlib import Path

import numpy as np

log = logging.getLogger(__name__)

MODULE_ROOT = Path(__file__).resolve().parents[2]
CACHE_DIR = MODULE_ROOT / "data" / "cache" / "embeddings"
DEFAULT_MODEL = "all-MiniLM-L6-v2"
MODEL_ENV = "EMBEDDING_MODEL"
BATCH_SIZE = 64


def model_name() -> str:
    return os.getenv(MODEL_ENV) or DEFAULT_MODEL


@lru_cache(maxsize=4)
def _model(name: str):
    from sentence_transformers import SentenceTransformer

    return SentenceTransformer(name, device="cpu")


class Encoder:
    """Normalised sentence vectors (cosine = dot product)."""

    def __init__(self, name: str | None = None, cache_dir: Path | None = CACHE_DIR):
        self.name = name or model_name()
        self.cache_dir = cache_dir

    def encode(self, texts: list[str]) -> np.ndarray:
        if not texts:
            return np.zeros((0, 1), dtype=np.float32)
        vecs = _model(self.name).encode(texts, batch_size=BATCH_SIZE, normalize_embeddings=True,
                                        convert_to_numpy=True, show_progress_bar=False)
        return vecs.astype(np.float32)

    def encode_cached(self, key: str, texts: list[str]) -> np.ndarray:
        """encode(), cached under (model, key, hash of texts) as float16."""
        if not self.cache_dir or not texts:
            return self.encode(texts)
        digest = hashlib.sha1("\n".join(texts).encode("utf-8")).hexdigest()[:16]
        safe = lambda s: re.sub(r"[^\w.-]", "_", s)  # noqa: E731
        file = self.cache_dir / safe(self.name) / f"{safe(key)}-{digest}.npy"
        if file.exists():
            return np.load(file).astype(np.float32)
        vecs = self.encode(texts)
        file.parent.mkdir(parents=True, exist_ok=True)
        np.save(file, vecs.astype(np.float16))
        return vecs


def cosine(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """Rows of a against rows of b (both already normalised)."""
    if len(a) == 0 or len(b) == 0:
        return np.zeros((len(a), len(b)), dtype=np.float32)
    return a @ b.T
