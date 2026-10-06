"""Schemas for identity/write clarification continuation."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

from semantic_memory.models.enums import (
    WriteClarificationResolution,
    WriteClarificationStatus,
)
from semantic_memory.schemas.common import MutationEnvelope
from semantic_memory.schemas.dry_run import OperationMode
from semantic_memory.schemas.statements import AssertStatementResponse


class WriteClarificationInfo(BaseModel):
    clarification_request_id: uuid.UUID
    ambiguous_path: Literal["subject", "object"]
    operation_mode: OperationMode
    status: WriteClarificationStatus
    expires_at: datetime
    candidate_entity_ids: list[uuid.UUID] = Field(default_factory=list)
    question: str | None = None


class AnswerIdentityClarificationRequest(MutationEnvelope):
    clarification_request_id: uuid.UUID
    resolution: WriteClarificationResolution
    chosen_entity_id: uuid.UUID | None = None

    @model_validator(mode="after")
    def _resolution_fields(self) -> AnswerIdentityClarificationRequest:
        if self.resolution == WriteClarificationResolution.CHOSEN_ENTITY:
            if self.chosen_entity_id is None:
                raise ValueError("chosen_entity_id is required for chosen_entity resolution")
        elif self.chosen_entity_id is not None:
            raise ValueError("chosen_entity_id is only valid for chosen_entity resolution")
        return self


class AnswerIdentityClarificationResponse(BaseModel):
    clarification_request_id: uuid.UUID
    status: WriteClarificationStatus
    resolution: WriteClarificationResolution | None = None
    assert_result: AssertStatementResponse | None = None
    # When resume hits fresh ambiguity or staleness, a new open handle is issued.
    open_clarification: WriteClarificationInfo | None = None
    request_id: uuid.UUID
    message: str | None = None
    details: dict[str, Any] = Field(default_factory=dict)
