"""Detect YAML/JSON package formats from stored source revision content."""

from __future__ import annotations

import json
from enum import StrEnum
from typing import Any

from semantic_memory.exceptions import UnsupportedSourceFormatError


class KnowledgePackageFormat(StrEnum):
    YAML = "yaml"
    JSON = "json"


def detect_package_format(content: str, *, hint: str | None = None) -> KnowledgePackageFormat:
    """Detect supported package format.

    ``hint`` may come from revision metadata (e.g. ``metadata_json['package_format']``)
    or original filename extension. Content sniffing is the fallback.
    """
    normalized_hint = (hint or "").strip().lower()
    if normalized_hint in {"yaml", "yml", "application/yaml", "text/yaml"}:
        return KnowledgePackageFormat.YAML
    if normalized_hint in {"json", "application/json", "text/json"}:
        return KnowledgePackageFormat.JSON

    stripped = content.lstrip()
    if not stripped:
        raise UnsupportedSourceFormatError(
            "Source content is empty; YAML/JSON package required",
            details={"hint": hint},
        )

    # Prefer JSON when the document is unambiguously JSON.
    if stripped[0] in "{[":
        try:
            json.loads(content)
            return KnowledgePackageFormat.JSON
        except json.JSONDecodeError:
            pass

    # YAML is a superset of JSON; only claim YAML after a successful parse attempt
    # by the structural parser. Here we accept YAML-looking content.
    if stripped[0] in "#'\"-|" or ":" in stripped.split("\n", 1)[0]:
        return KnowledgePackageFormat.YAML

    raise UnsupportedSourceFormatError(
        "Unsupported source package format; only YAML and JSON are supported in V1",
        details={"hint": hint, "content_prefix": stripped[:80]},
    )


def load_json_document(content: str) -> Any:
    try:
        return json.loads(content)
    except json.JSONDecodeError as exc:
        raise UnsupportedSourceFormatError(
            "Content detected as JSON but failed to parse",
            details={"error": str(exc)},
        ) from exc
