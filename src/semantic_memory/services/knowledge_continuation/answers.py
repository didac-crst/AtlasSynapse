"""Validate and persist package clarification answers."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.orm import Session

from semantic_memory.exceptions import ValidationFailedError
from semantic_memory.models.enums import (
    KnowledgeIngestionClarificationKind,
    KnowledgeIngestionClarificationStatus,
    SemanticClarificationStatus,
)
from semantic_memory.repositories.entities import EntityRepository
from semantic_memory.repositories.governance import GovernanceRepository
from semantic_memory.schemas.semantic_review import AnswerSemanticClarificationRequest
from semantic_memory.services.knowledge_continuation.schemas import (
    ClarificationAnswerInput,
    IdentityAnswerResolution,
)
from semantic_memory.services.knowledge_ingestion import KnowledgeIngestionService
from semantic_memory.services.proposals import ProposalService

_ONTOLOGY_TERMINAL = frozenset(
    {
        SemanticClarificationStatus.ANSWERED.value,
        SemanticClarificationStatus.RESOLVED.value,
        SemanticClarificationStatus.SUPERSEDED.value,
    }
)


def apply_clarification_answers(
    session: Session,
    *,
    ingestion_id: uuid.UUID,
    answers: list[ClarificationAnswerInput],
    actor_key: str,
) -> list[uuid.UUID]:
    """Apply answers; return clarification ids that were newly answered (or idempotent)."""
    ki = KnowledgeIngestionService(session)
    ki.get_ingestion(ingestion_id)
    applied: list[uuid.UUID] = []
    for item in answers:
        row = ki.get_clarification(item.clarification_id)
        if row.ingestion_id != ingestion_id:
            raise ValidationFailedError(
                "clarification_id must belong to ingestion_id",
                details={
                    "ingestion_id": str(ingestion_id),
                    "clarification_id": str(item.clarification_id),
                },
            )
        if row.clarification_kind == KnowledgeIngestionClarificationKind.IDENTITY.value:
            payload = _normalize_identity_answer(session, row.question_payload, item.answer_payload)
            ki.answer_clarification(row.id, answer_payload=payload)
            applied.append(row.id)
        elif row.clarification_kind == KnowledgeIngestionClarificationKind.ONTOLOGY.value:
            package_payload = _apply_ontology_package_answer(
                session,
                actor_key=actor_key,
                row=row,
                answer_payload=item.answer_payload,
            )
            ki.answer_clarification(row.id, answer_payload=package_payload)
            applied.append(row.id)
        else:
            ki.answer_clarification(row.id, answer_payload=item.answer_payload)
            applied.append(row.id)
    return applied


def reconcile_ontology_package_clarifications(
    session: Session, *, ingestion_id: uuid.UUID
) -> list[uuid.UUID]:
    """Advance open package ontology clarifications whose linked handle is already terminal.

    Covers crash/retry between ``answer_semantic_clarification`` success and marking the
    package row answered. Does not call the one-shot ontology answer again.
    """
    ki = KnowledgeIngestionService(session)
    ki.get_ingestion(ingestion_id)
    governance = GovernanceRepository(session)
    reconciled: list[uuid.UUID] = []
    for row in ki.list_clarifications(ingestion_id):
        if row.status != KnowledgeIngestionClarificationStatus.OPEN.value:
            continue
        if row.clarification_kind != KnowledgeIngestionClarificationKind.ONTOLOGY.value:
            continue
        ont_id = row.ontology_clarification_request_id
        if ont_id is None:
            continue
        linked = governance.get_clarification_request(ont_id)
        if linked is None:
            continue
        if linked.status not in _ONTOLOGY_TERMINAL:
            continue
        payload = {
            "reconciled": True,
            "delegated": True,
            "ontology_clarification_request_id": str(ont_id),
            "ontology_status": linked.status,
        }
        ki.answer_clarification(row.id, answer_payload=payload)
        reconciled.append(row.id)
    return reconciled


def _apply_ontology_package_answer(
    session: Session,
    *,
    actor_key: str,
    row: Any,
    answer_payload: dict[str, Any],
) -> dict[str, Any]:
    ont_id = row.ontology_clarification_request_id
    if ont_id is None:
        raise ValidationFailedError(
            "Ontology package clarification lacks ontology_clarification_request_id",
            details={"clarification_id": str(row.id)},
        )
    linked = GovernanceRepository(session).get_clarification_request(ont_id)
    if linked is None:
        raise ValidationFailedError(
            "Linked ontology clarification request was not found",
            details={"ontology_clarification_request_id": str(ont_id)},
        )

    # Crash/retry: ontology handle already terminal — do not replay one-shot answer.
    if linked.status in _ONTOLOGY_TERMINAL:
        package_payload = dict(answer_payload)
        package_payload["reconciled"] = True
        package_payload["delegated"] = True
        package_payload["ontology_clarification_request_id"] = str(ont_id)
        package_payload["ontology_status"] = linked.status
        return package_payload

    if linked.status != SemanticClarificationStatus.OPEN.value:
        raise ValidationFailedError(
            "Linked ontology clarification is not open for answering",
            details={
                "ontology_clarification_request_id": str(ont_id),
                "ontology_status": linked.status,
            },
        )

    response_text = str(
        answer_payload.get("response") or answer_payload.get("answer") or ""
    ).strip()
    if not response_text:
        raise ValidationFailedError(
            "Ontology clarification answer requires response text",
            details={"ontology_clarification_request_id": str(ont_id)},
        )
    ProposalService(session).answer_semantic_clarification(
        AnswerSemanticClarificationRequest(
            actor_key=actor_key,
            request_id=uuid.uuid4(),
            idempotency_key=f"ki-ont-answer:{ont_id}:{uuid.uuid4()}",
            clarification_request_id=ont_id,
            response=response_text,
            evidence_refs=list(answer_payload.get("evidence_refs") or []),
            proposed_revision=answer_payload.get("proposed_revision"),
        )
    )
    package_payload = dict(answer_payload)
    package_payload["delegated"] = True
    package_payload["ontology_clarification_request_id"] = str(ont_id)
    return package_payload


def _normalize_identity_answer(
    session: Session,
    question_payload: dict[str, Any] | None,
    answer_payload: dict[str, Any],
) -> dict[str, Any]:
    question = question_payload if isinstance(question_payload, dict) else {}
    resolution_raw = str(answer_payload.get("resolution") or "").strip()
    try:
        resolution = IdentityAnswerResolution(resolution_raw)
    except ValueError as exc:
        raise ValidationFailedError(
            "Invalid identity clarification resolution",
            details={
                "resolution": resolution_raw,
                "allowed": [m.value for m in IdentityAnswerResolution],
            },
        ) from exc

    offered = {
        uuid.UUID(str(item["entity_id"]))
        for item in (question.get("candidate_entities") or [])
        if isinstance(item, dict) and item.get("entity_id")
    }

    if resolution == IdentityAnswerResolution.CHOSEN_ENTITY:
        chosen_raw = answer_payload.get("chosen_entity_id")
        if not chosen_raw:
            raise ValidationFailedError(
                "chosen_entity requires chosen_entity_id",
                details={},
            )
        chosen = uuid.UUID(str(chosen_raw))
        if chosen not in offered:
            raise ValidationFailedError(
                "chosen_entity_id is not in the offered candidate set",
                details={
                    "chosen_entity_id": str(chosen),
                    "offered": [str(x) for x in sorted(offered, key=str)],
                },
            )
        entity = EntityRepository(session).get(chosen)
        if entity is None:
            raise ValidationFailedError(
                "chosen_entity_id does not exist",
                details={"chosen_entity_id": str(chosen)},
            )
        return {
            "resolution": resolution.value,
            "chosen_entity_id": str(chosen),
        }

    if resolution == IdentityAnswerResolution.CREATE_NEW:
        return {"resolution": resolution.value}

    if resolution == IdentityAnswerResolution.REJECT:
        return {"resolution": resolution.value}

    raise ValidationFailedError("Unhandled identity resolution", details={})
