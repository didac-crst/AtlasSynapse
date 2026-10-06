"""Deterministic string normalization helpers."""

from __future__ import annotations

import re

_CLASS_KEY_RE = re.compile(r"^[A-Z][A-Za-z0-9]*$")
_PREDICATE_KEY_RE = re.compile(r"^[a-z][A-Za-z0-9]*$")
_IDENT_PARTS_RE = re.compile(r"[^A-Za-z0-9]+")


def normalize_text(value: str) -> str:
    """Collapse whitespace and casefold for deterministic identity matching."""
    return " ".join(value.strip().split()).casefold()


def is_pascal_case_key(value: str) -> bool:
    return bool(_CLASS_KEY_RE.fullmatch(value))


def is_lower_camel_case_key(value: str) -> bool:
    return bool(_PREDICATE_KEY_RE.fullmatch(value))


def suggest_class_key(value: str) -> str:
    """Suggest a PascalCase ontology class key."""
    parts = [part for part in _IDENT_PARTS_RE.split(value.strip()) if part]
    if not parts:
        return value
    return "".join(part[:1].upper() + part[1:] for part in parts)


def suggest_predicate_key(value: str) -> str:
    """Suggest a lowerCamelCase ontology predicate key."""
    stripped = value.strip()
    if re.fullmatch(r"[A-Za-z][A-Za-z0-9]*", stripped):
        return stripped[:1].lower() + stripped[1:]
    parts = [part for part in _IDENT_PARTS_RE.split(stripped) if part]
    if not parts:
        return value
    head = parts[0][:1].lower() + parts[0][1:]
    tail = "".join(part[:1].upper() + part[1:] for part in parts[1:])
    return head + tail
