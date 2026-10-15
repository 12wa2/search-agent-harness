"""Deterministic local text embedding, so the vector channel needs no model download.

This is deliberately *not* a trained semantic model: it is a hashed bag-of-tokens
projection with sublinear term frequency and L2 normalisation. Its job is to make
the ``dense_vector`` / kNN pipeline -- index mapping, neighbour search, rank
fusion, tests, CI -- real and reproducible without shipping a model or an API key.

Swapping in a real model means replacing ``embed_text`` (for example
``BAAI/bge-small-zh`` through sentence-transformers, or a hosted embedding API)
and re-indexing; nothing else in the backend changes.
"""

from __future__ import annotations

import hashlib
import math

from search_agent.tools.text import tokens

__all__ = ["EMBEDDING_DIMS", "cosine", "embed_text"]

#: Vector width; must match ``dense_vector.dims`` in the index mapping.
EMBEDDING_DIMS = 256


def _bucket(token: str, dims: int) -> int:
    """Stable bucket for one token (blake2b, so it never depends on PYTHONHASHSEED)."""
    digest = hashlib.blake2b(token.encode("utf-8"), digest_size=8).digest()
    return int.from_bytes(digest, "big") % dims


def embed_text(text: str, *, dims: int = EMBEDDING_DIMS) -> list[float]:
    """L2-normalised vector for ``text``; identical input always gives identical output."""
    counts: dict[str, int] = {}
    for token in tokens(text):
        counts[token] = counts.get(token, 0) + 1
    vector = [0.0] * dims
    for token, count in counts.items():
        vector[_bucket(token, dims)] += 1.0 + math.log(count)
    norm = math.sqrt(sum(value * value for value in vector))
    if norm == 0.0:
        return vector
    return [value / norm for value in vector]


def cosine(left: list[float], right: list[float]) -> float:
    """Cosine similarity for two (already normalised) vectors."""
    if len(left) != len(right):
        raise ValueError("vectors must share the same dimensions")
    return sum(a * b for a, b in zip(left, right, strict=True))
