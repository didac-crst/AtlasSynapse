"""Server-owned identity helpers for statement write paths.

Callers must not resolve or create entities locally before assert/supersede.
Unresolved sides go through EntityService.resolve_or_clarify_for_write.
"""

from __future__ import annotations

import uuid
from typing import Any

from semantic_memory.exceptions import AmbiguousEntityError
from semantic_memory.observability.logging import get_logger
from semantic_memory.schemas.identity import IdentityResolutionResult

logger = get_logger("semantic_memory.identity.write")

CLARIFY_MESSAGE = "Identity clarification required"
CLARIFY_ERROR_CODE = AmbiguousEntityError.error_code


def identity_clarify_details(
    *,
    subject_identity: IdentityResolutionResult | None,
    object_identity: IdentityResolutionResult | None,
) -> dict[str, Any]:
    """Shared clarify payload for single-assert, batch, and supersede paths."""
    return {
        "subject_identity": None
        if subject_identity is None
        else subject_identity.model_dump(mode="json"),
        "object_identity": None
        if object_identity is None
        else object_identity.model_dump(mode="json"),
    }


def log_write_side_resolution(
    *,
    side: str,
    resolution: str | None,
    action: str | None,
    clarify: bool,
    entity_created: bool,
    entity_id: uuid.UUID | None,
    decision_basis: str | None,
    request_id: uuid.UUID,
) -> None:
    """Emit MATCH / NO_MATCH / AMBIGUOUS observability for real writes."""
    logger.info(
        "identity_write_side",
        extra={
            "event": "identity_write_side",
            "side": side,
            "resolution": resolution,
            "action": action,
            "clarify": clarify,
            "entity_created": entity_created,
            "entity_id": None if entity_id is None else str(entity_id),
            "decision_basis": decision_basis,
            "request_id": str(request_id),
        },
    )
