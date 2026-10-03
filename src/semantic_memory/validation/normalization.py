"""Deterministic string normalization helpers."""

from __future__ import annotations


def normalize_text(value: str) -> str:
    """Collapse whitespace and casefold for deterministic identity matching."""
    return " ".join(value.strip().split()).casefold()
