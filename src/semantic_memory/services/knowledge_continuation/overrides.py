"""Load answered package clarifications as resolver overrides."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.orm import Session

from semantic_memory.models.enums import (
    KnowledgeIngestionClarificationKind,
    KnowledgeIngestionClarificationStatus,
)
from semantic_memory.services.knowledge_continuation.schemas import (
    IdentityAnswerResolution,
    IdentityOverride,
    ResolutionOverrides,
)
from semantic_memory.services.knowledge_ingestion import KnowledgeIngestionService


def load_resolution_overrides(session: Session, ingestion_id: uuid.UUID) -> ResolutionOverrides:
    ki = KnowledgeIngestionService(session)
    overrides = ResolutionOverrides()
    for row in ki.list_clarifications(ingestion_id):
        if row.status != KnowledgeIngestionClarificationStatus.ANSWERED.value:
            continue
        if row.clarification_kind != KnowledgeIngestionClarificationKind.IDENTITY.value:
            continue
        answer = row.answer_payload if isinstance(row.answer_payload, dict) else {}
        question = row.question_payload if isinstance(row.question_payload, dict) else {}
        field = str(question.get("field") or "")
        if field not in {"subject", "object"}:
            continue
        candidate_raw = question.get("candidate_id") or (
            None if row.root_candidate_id is None else str(row.root_candidate_id)
        )
        if not candidate_raw:
            continue
        candidate_id = uuid.UUID(str(candidate_raw))
        resolution_raw = str(answer.get("resolution") or "")
        try:
            resolution = IdentityAnswerResolution(resolution_raw)
        except ValueError:
            continue
        chosen: uuid.UUID | None = None
        if answer.get("chosen_entity_id"):
            chosen = uuid.UUID(str(answer["chosen_entity_id"]))
        offered = _offered_ids(question)
        override = IdentityOverride(
            candidate_id=candidate_id,
            field=field,  # type: ignore[arg-type]
            resolution=resolution,
            chosen_entity_id=chosen,
            clarification_id=row.id,
            offered_entity_ids=offered,
        )
        overrides.identity_by_candidate_field[f"{candidate_id}:{field}"] = override
    return overrides


def _offered_ids(question: dict[str, Any]) -> list[uuid.UUID]:
    out: list[uuid.UUID] = []
    for item in question.get("candidate_entities") or []:
        if isinstance(item, dict) and item.get("entity_id"):
            out.append(uuid.UUID(str(item["entity_id"])))
    return out
