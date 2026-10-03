"""Actor request/response schemas."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, Field

from semantic_memory.models.enums import ActorStatus, ActorType


class ActorEnsureRequest(BaseModel):
    key: str = Field(min_length=1, description="Stable actor key; stored as actor.name")
    actor_type: ActorType = ActorType.AGENT
    capabilities: list[str] = Field(default_factory=list)
    status: ActorStatus = ActorStatus.ACTIVE


class ActorResponse(BaseModel):
    id: uuid.UUID
    key: str
    actor_type: ActorType
    status: ActorStatus
    capabilities: list[str]
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}
