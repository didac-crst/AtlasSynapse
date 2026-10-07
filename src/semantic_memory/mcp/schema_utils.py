"""Build MCP tool input schemas from Pydantic request models."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from pydantic import BaseModel


def mcp_payload_schema(
    model: type[BaseModel],
    *,
    exclude_fields: frozenset[str] = frozenset({"actor_key"}),
) -> dict[str, Any]:
    """Wrap a mutation request model as ``{payload: {...}}`` for MCP tools.

    ``actor_key`` is omitted: the MCP adapter injects the configured actor.
    """
    raw = model.model_json_schema(mode="validation")
    defs = deepcopy(raw.get("$defs") or raw.get("definitions") or {})
    properties = deepcopy(raw.get("properties") or {})
    for field_name in exclude_fields:
        properties.pop(field_name, None)
    required = [
        name for name in (raw.get("required") or []) if name not in exclude_fields
    ]
    payload: dict[str, Any] = {
        "type": "object",
        "properties": properties,
        "additionalProperties": False,
        "description": (
            f"{model.__name__} fields. Do not pass actor_key; the MCP server injects it."
        ),
    }
    if required:
        payload["required"] = required
    if defs:
        payload["$defs"] = defs
    return {
        "type": "object",
        "properties": {"payload": payload},
        "required": ["payload"],
        "additionalProperties": False,
    }
