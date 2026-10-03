"""Payload redaction for operation audit records."""

from __future__ import annotations

from typing import Any, Literal

RetentionMode = Literal["none", "redacted", "full"]

_SENSITIVE_KEY_FRAGMENTS = (
    "password",
    "secret",
    "token",
    "authorization",
    "api_key",
    "apikey",
    "credential",
    "private_key",
)

_REDACTED_TEXT_KEYS = frozenset({"excerpt", "object_string", "description", "title"})


def _is_sensitive_key(key: str) -> bool:
    lowered = key.casefold()
    return any(fragment in lowered for fragment in _SENSITIVE_KEY_FRAGMENTS)


def _redact_value(key: str, value: Any, *, mode: RetentionMode) -> Any:
    if _is_sensitive_key(key):
        return "[REDACTED]"
    if mode == "redacted" and key.casefold() in _REDACTED_TEXT_KEYS and isinstance(value, str):
        return "[REDACTED]"
    if isinstance(value, dict):
        return {
            str(child): _redact_value(str(child), child_value, mode=mode)
            for child, child_value in value.items()
        }
    if isinstance(value, list):
        return [_redact_value(key, item, mode=mode) for item in value]
    return value


def prepare_audit_payload(
    payload: dict[str, Any] | None, *, mode: RetentionMode
) -> dict[str, Any] | None:
    """Return a payload suitable for durable operation-log storage."""
    if payload is None or mode == "none":
        return None
    # Secrets are always stripped, even in full retention.
    effective: RetentionMode = "full" if mode == "full" else mode
    return {
        str(key): _redact_value(str(key), value, mode=effective) for key, value in payload.items()
    }
