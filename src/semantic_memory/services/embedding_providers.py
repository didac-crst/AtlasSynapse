"""Replaceable embedding providers. Optional; never required for correctness."""

from __future__ import annotations

import hashlib
import math
import re
from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class EmbeddedText:
    vector: list[float]
    model_key: str
    content_hash: str


@runtime_checkable
class EmbeddingProvider(Protocol):
    """Provider-neutral embedding interface.

    Implementations must not write to the database. Callers persist vectors.
    """

    @property
    def model_key(self) -> str: ...

    @property
    def dimensions(self) -> int: ...

    def embed(self, text: str) -> EmbeddedText: ...


class DisabledEmbeddingProvider:
    """No-op provider used when embeddings are turned off."""

    @property
    def model_key(self) -> str:
        return "disabled"

    @property
    def dimensions(self) -> int:
        return 0

    def embed(self, text: str) -> EmbeddedText:
        raise RuntimeError("Embedding provider is disabled")


class MockEmbeddingProvider:
    """Deterministic bag-of-tokens embedding for tests and local development."""

    def __init__(self, *, dimensions: int = 32, model_key: str = "mock-hash-v1") -> None:
        self._dimensions = dimensions
        self._model_key = model_key

    @property
    def model_key(self) -> str:
        return self._model_key

    @property
    def dimensions(self) -> int:
        return self._dimensions

    def embed(self, text: str) -> EmbeddedText:
        tokens = re.findall(r"[a-z0-9]+", text.lower())
        vector = [0.0] * self._dimensions
        if not tokens:
            tokens = ["empty"]
        for token in tokens:
            digest = hashlib.sha256(token.encode("utf-8")).digest()
            index = digest[0] % self._dimensions
            sign = 1.0 if digest[1] % 2 == 0 else -1.0
            vector[index] += sign
        norm = math.sqrt(sum(value * value for value in vector)) or 1.0
        normalized = [value / norm for value in vector]
        content_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
        return EmbeddedText(
            vector=normalized,
            model_key=self._model_key,
            content_hash=content_hash,
        )


def cosine_similarity(left: list[float], right: list[float]) -> float:
    if not left or not right or len(left) != len(right):
        return 0.0
    dot = sum(a * b for a, b in zip(left, right, strict=True))
    left_norm = math.sqrt(sum(a * a for a in left)) or 1.0
    right_norm = math.sqrt(sum(b * b for b in right)) or 1.0
    return dot / (left_norm * right_norm)
