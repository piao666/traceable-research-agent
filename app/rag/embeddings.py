"""Deterministic lightweight bag-of-words embeddings."""

from __future__ import annotations

import math
import re
from collections import Counter


TOKEN_RE = re.compile(r"[A-Za-z0-9_]+")


def tokenize(text: str) -> list[str]:
    """Tokenize text into lowercase word-like units."""

    return [token.lower() for token in TOKEN_RE.findall(text)]


def embed_text(text: str) -> dict[str, float]:
    """Return a normalized token-frequency vector."""

    counts = Counter(tokenize(text))
    if not counts:
        return {}
    norm = math.sqrt(sum(value * value for value in counts.values()))
    if norm == 0:
        return {}
    return {token: value / norm for token, value in counts.items()}


def cosine_similarity(left: dict[str, float], right: dict[str, float]) -> float:
    """Compute cosine similarity for sparse normalized vectors."""

    if not left or not right:
        return 0.0
    if len(left) > len(right):
        left, right = right, left
    return sum(value * right.get(token, 0.0) for token, value in left.items())
