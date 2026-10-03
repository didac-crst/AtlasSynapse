"""Literal and object normalization for statement identity."""

from __future__ import annotations

import json
import uuid
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from semantic_memory.models.enums import ValueKind
from semantic_memory.validation.normalization import normalize_text


def normalize_confidence(value: Decimal | None) -> Decimal | None:
    """Normalize confidence to four decimal places in ``[0, 1]``."""
    if value is None:
        return None
    try:
        quantized = Decimal(value).quantize(Decimal("0.0001"))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError("confidence must be a finite decimal") from exc
    if quantized < 0 or quantized > 1:
        raise ValueError("confidence must be between 0 and 1 inclusive")
    return quantized


def normalize_object_identity(
    *,
    value_kind: ValueKind | str,
    object_entity_id: uuid.UUID | None = None,
    object_string: str | None = None,
    object_number: Decimal | None = None,
    object_boolean: bool | None = None,
    object_datetime: datetime | None = None,
    object_json: dict[str, Any] | list[Any] | None = None,
) -> str:
    """Return a stable normalized object key for semantic duplicate detection."""
    kind = ValueKind(value_kind)
    if kind == ValueKind.ENTITY:
        if object_entity_id is None:
            raise ValueError("entity object requires object_entity_id")
        return f"entity:{object_entity_id}"
    if kind == ValueKind.STRING:
        if object_string is None:
            raise ValueError("string object requires object_string")
        return f"string:{normalize_text(object_string)}"
    if kind == ValueKind.NUMBER:
        if object_number is None:
            raise ValueError("number object requires object_number")
        number = Decimal(object_number).normalize()
        return f"number:{format(number, 'f')}"
    if kind == ValueKind.BOOLEAN:
        if object_boolean is None:
            raise ValueError("boolean object requires object_boolean")
        return f"boolean:{str(object_boolean).lower()}"
    if kind == ValueKind.DATETIME:
        if object_datetime is None:
            raise ValueError("datetime object requires object_datetime")
        return f"datetime:{object_datetime.isoformat()}"
    if kind == ValueKind.JSON:
        if object_json is None:
            raise ValueError("json object requires object_json")
        encoded = json.dumps(object_json, sort_keys=True, separators=(",", ":"), default=str)
        return f"json:{encoded}"
    raise ValueError(f"Unsupported value kind '{kind}'")
