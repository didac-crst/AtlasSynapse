"""Thin MCP-facing tool adapters for knowledge-plane operations."""

from __future__ import annotations

import uuid
from collections.abc import Callable
from typing import Any

from pydantic import BaseModel, ValidationError
from sqlalchemy.orm import Session

from semantic_memory.config import get_settings
from semantic_memory.exceptions import DomainError, ValidationFailedError
from semantic_memory.models.enums import ActorType, ConflictStatus
from semantic_memory.schemas.actors import ActorEnsureRequest
from semantic_memory.schemas.batches import AssertBatchRequest
from semantic_memory.schemas.conflicts import (
    DismissConflictRequest,
    FindConflictsResponse,
    MergeEntityRequest,
    ResolveConflictRequest,
)
from semantic_memory.schemas.entities import (
    AddEntityAliasRequest,
    CreateEntityRequest,
    EntityResponse,
)
from semantic_memory.schemas.errors import ErrorEnvelope
from semantic_memory.schemas.feedback import ReportFeedbackRequest
from semantic_memory.schemas.ontology import (
    OntologyClassResponse,
    OntologyContextResponse,
    OntologyPredicateResponse,
    OntologySearchResponse,
)
from semantic_memory.schemas.proposals import (
    ApplyProposalRequest,
    ProposalResponse,
    ProposeAliasRequest,
    ProposeClassParentRequest,
    ProposeClassRequest,
    ProposeConstraintRequest,
    ProposePredicateRequest,
)
from semantic_memory.schemas.provenance import (
    AddEvidenceRequest,
    EnsureSourceRequest,
    ExplainStatementResponse,
    GetSourceContentRequest,
    GetSourceContentResponse,
    IngestSourceContentRequest,
    SearchSourceContentRequest,
    SearchSourceContentResponse,
)
from semantic_memory.schemas.retrieval import (
    RelevantContextRequest,
    SearchEntitiesRequest,
    SearchSemanticMemoryRequest,
    SearchStatementsRequest,
)
from semantic_memory.schemas.statements import (
    AssertStatementRequest,
    CorrectStatementRequest,
    RetractStatementRequest,
    StatementResponse,
    SupersedeStatementRequest,
    TimelineResponse,
)
from semantic_memory.schemas.write_clarifications import AnswerIdentityClarificationRequest
from semantic_memory.services.actors import ActorService
from semantic_memory.services.batches import BatchService
from semantic_memory.services.conflicts import ConflictService
from semantic_memory.services.entities import EntityService
from semantic_memory.services.feedback import FeedbackService
from semantic_memory.services.ontology import OntologyService
from semantic_memory.services.proposals import ProposalService
from semantic_memory.services.provenance import ProvenanceService
from semantic_memory.services.retrieval import RetrievalService
from semantic_memory.services.statements import StatementService
from semantic_memory.services.write_clarifications import WriteClarificationService


def _error(exc: DomainError) -> dict[str, Any]:
    return ErrorEnvelope(
        error_code=exc.error_code,
        message=exc.message,
        details=exc.details,
        request_id=exc.request_id,
        retryable=exc.retryable,
    ).model_dump(mode="json")


def prepare_mcp_mutation_payload(session: Session, payload: dict[str, Any]) -> dict[str, Any]:
    """Inject the configured MCP actor and ensure it exists.

    MCP clients must not guess ``actor_key``. HTTP/API callers still supply it
    explicitly for multi-actor operation.
    """
    settings = get_settings()
    ActorService(session).ensure(
        ActorEnsureRequest(
            key=settings.mcp_actor_key,
            actor_type=ActorType.AGENT,
        )
    )
    prepared = dict(payload)
    prepared["actor_key"] = settings.mcp_actor_key
    return prepared


def _run_mutation[T: BaseModel](
    session: Session,
    payload: dict[str, Any],
    operation: Callable[[dict[str, Any]], T],
) -> dict[str, Any]:
    try:
        prepared = prepare_mcp_mutation_payload(session, payload)
        result = operation(prepared)
        session.commit()
        return result.model_dump(mode="json")
    except DomainError as exc:
        session.commit()
        return _error(exc)
    except (ValidationError, ValueError) as exc:
        session.rollback()
        return _error(
            ValidationFailedError(
                "Request validation failed",
                details={"error": str(exc)},
            )
        )
    except Exception:
        session.rollback()
        raise


class RetrievalMCPTools:
    """Translate MCP tool calls into retrieval service operations."""

    def __init__(self, session: Session) -> None:
        self._retrieval = RetrievalService(session)

    def search_entities(self, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            result = self._retrieval.search_entities(SearchEntitiesRequest.model_validate(payload))
            return result.model_dump(mode="json")
        except (DomainError, ValidationError, ValueError) as exc:
            if isinstance(exc, DomainError):
                return _error(exc)
            return _error(
                ValidationFailedError("Invalid entity search", details={"error": str(exc)})
            )

    def search_statements(self, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            result = self._retrieval.search_statements(
                SearchStatementsRequest.model_validate(payload)
            )
            return result.model_dump(mode="json")
        except (DomainError, ValidationError, ValueError) as exc:
            if isinstance(exc, DomainError):
                return _error(exc)
            return _error(
                ValidationFailedError("Invalid statement search", details={"error": str(exc)})
            )

    def get_entity_neighborhood(self, entity_id: str, *, limit: int = 50) -> dict[str, Any]:
        try:
            result = self._retrieval.get_entity_neighborhood(uuid.UUID(entity_id), limit=limit)
            return result.model_dump(mode="json")
        except (DomainError, ValueError) as exc:
            if isinstance(exc, DomainError):
                return _error(exc)
            return _error(
                ValidationFailedError(
                    "Invalid neighborhood lookup",
                    details={"entity_id": entity_id, "error": str(exc)},
                )
            )

    def search_semantic_memory(self, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            result = self._retrieval.search_semantic_memory(
                SearchSemanticMemoryRequest.model_validate(payload)
            )
            return result.model_dump(mode="json")
        except (DomainError, ValidationError, ValueError) as exc:
            if isinstance(exc, DomainError):
                return _error(exc)
            return _error(
                ValidationFailedError("Invalid semantic memory search", details={"error": str(exc)})
            )

    def get_relevant_context(self, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            result = self._retrieval.get_relevant_context(
                RelevantContextRequest.model_validate(payload)
            )
            return result.model_dump(mode="json")
        except (DomainError, ValidationError, ValueError) as exc:
            if isinstance(exc, DomainError):
                return _error(exc)
            return _error(
                ValidationFailedError(
                    "Invalid relevant context request", details={"error": str(exc)}
                )
            )


class EntityMCPTools:
    """Translate MCP tool calls into entity service operations."""

    def __init__(self, session: Session) -> None:
        self._session = session
        self._entities = EntityService(session)

    def create_entity(self, payload: dict[str, Any]) -> dict[str, Any]:
        return _run_mutation(
            self._session,
            payload,
            lambda p: self._entities.create_entity(CreateEntityRequest.model_validate(p)),
        )

    def get_entity(self, entity_id: str) -> dict[str, Any]:
        try:
            result: EntityResponse = self._entities.get(uuid.UUID(entity_id))
            return result.model_dump(mode="json")
        except (DomainError, ValueError) as exc:
            if isinstance(exc, DomainError):
                return _error(exc)
            return _error(
                ValidationFailedError(
                    "Invalid entity id",
                    details={"entity_id": entity_id, "error": str(exc)},
                )
            )

    def merge_entity(self, payload: dict[str, Any]) -> dict[str, Any]:
        return _run_mutation(
            self._session,
            payload,
            lambda p: self._entities.merge_entity(MergeEntityRequest.model_validate(p)),
        )

    def add_entity_alias(self, payload: dict[str, Any]) -> dict[str, Any]:
        return _run_mutation(
            self._session,
            payload,
            lambda p: self._entities.add_entity_alias(AddEntityAliasRequest.model_validate(p)),
        )


class StatementMCPTools:
    """Translate MCP tool calls into statement and provenance operations."""

    def __init__(self, session: Session) -> None:
        self._session = session
        self._statements = StatementService(session)
        self._provenance = ProvenanceService(session)
        self._conflicts = ConflictService(session)

    def assert_statement(self, payload: dict[str, Any]) -> dict[str, Any]:
        return _run_mutation(
            self._session,
            payload,
            lambda p: self._statements.assert_statement(AssertStatementRequest.model_validate(p)),
        )

    def answer_identity_clarification(self, payload: dict[str, Any]) -> dict[str, Any]:
        return _run_mutation(
            self._session,
            payload,
            lambda p: WriteClarificationService(self._session).answer(
                AnswerIdentityClarificationRequest.model_validate(p)
            ),
        )

    def get_statement(self, statement_id: str) -> dict[str, Any]:
        try:
            result: StatementResponse = self._statements.get(uuid.UUID(statement_id))
            return result.model_dump(mode="json")
        except (DomainError, ValueError) as exc:
            if isinstance(exc, DomainError):
                return _error(exc)
            return _error(
                ValidationFailedError(
                    "Invalid statement id",
                    details={"statement_id": statement_id, "error": str(exc)},
                )
            )

    def explain_statement(self, statement_id: str) -> dict[str, Any]:
        try:
            result: ExplainStatementResponse = self._provenance.explain_statement(
                uuid.UUID(statement_id)
            )
            return result.model_dump(mode="json")
        except (DomainError, ValueError) as exc:
            if isinstance(exc, DomainError):
                return _error(exc)
            return _error(
                ValidationFailedError(
                    "Invalid statement id",
                    details={"statement_id": statement_id, "error": str(exc)},
                )
            )

    def add_evidence(self, payload: dict[str, Any]) -> dict[str, Any]:
        return _run_mutation(
            self._session,
            payload,
            lambda p: self._provenance.add_evidence(AddEvidenceRequest.model_validate(p)),
        )

    def ensure_source(self, payload: dict[str, Any]) -> dict[str, Any]:
        return _run_mutation(
            self._session,
            payload,
            lambda p: self._provenance.ensure_source(EnsureSourceRequest.model_validate(p)),
        )

    def ingest_source_content(self, payload: dict[str, Any]) -> dict[str, Any]:
        return _run_mutation(
            self._session,
            payload,
            lambda p: self._provenance.ingest_source_content(
                IngestSourceContentRequest.model_validate(p)
            ),
        )

    def get_source_content(self, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            result: GetSourceContentResponse = self._provenance.get_source_content(
                GetSourceContentRequest.model_validate(payload)
            )
            return result.model_dump(mode="json")
        except (DomainError, ValidationError, ValueError) as exc:
            if isinstance(exc, DomainError):
                return _error(exc)
            return _error(
                ValidationFailedError(
                    "Request validation failed",
                    details={"error": str(exc)},
                )
            )

    def search_source_content(self, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            result: SearchSourceContentResponse = self._provenance.search_source_content(
                SearchSourceContentRequest.model_validate(payload)
            )
            return result.model_dump(mode="json")
        except (DomainError, ValidationError, ValueError) as exc:
            if isinstance(exc, DomainError):
                return _error(exc)
            return _error(
                ValidationFailedError(
                    "Request validation failed",
                    details={"error": str(exc)},
                )
            )

    def supersede_statement(self, payload: dict[str, Any]) -> dict[str, Any]:
        return _run_mutation(
            self._session,
            payload,
            lambda p: self._statements.supersede_statement(
                SupersedeStatementRequest.model_validate(p)
            ),
        )

    def correct_statement(self, payload: dict[str, Any]) -> dict[str, Any]:
        return _run_mutation(
            self._session,
            payload,
            lambda p: self._statements.correct_statement(CorrectStatementRequest.model_validate(p)),
        )

    def retract_statement(self, payload: dict[str, Any]) -> dict[str, Any]:
        return _run_mutation(
            self._session,
            payload,
            lambda p: self._statements.retract_statement(RetractStatementRequest.model_validate(p)),
        )

    def get_timeline(self, entity_id: str) -> dict[str, Any]:
        try:
            result: TimelineResponse = self._statements.get_timeline(uuid.UUID(entity_id))
            return result.model_dump(mode="json")
        except (DomainError, ValueError) as exc:
            if isinstance(exc, DomainError):
                return _error(exc)
            return _error(
                ValidationFailedError(
                    "Invalid entity id",
                    details={"entity_id": entity_id, "error": str(exc)},
                )
            )

    def find_conflicts(
        self,
        *,
        entity_id: str | None = None,
        statement_id: str | None = None,
        status: str | None = "open",
    ) -> dict[str, Any]:
        try:
            resolved_status: ConflictStatus | None
            if status is None:
                resolved_status = None
            else:
                resolved_status = ConflictStatus(status)
            result: FindConflictsResponse = self._conflicts.find_conflicts(
                entity_id=None if entity_id is None else uuid.UUID(entity_id),
                statement_id=None if statement_id is None else uuid.UUID(statement_id),
                status=resolved_status,
            )
            return result.model_dump(mode="json")
        except (DomainError, ValueError) as exc:
            if isinstance(exc, DomainError):
                return _error(exc)
            return _error(
                ValidationFailedError(
                    "Invalid conflict query",
                    details={"error": str(exc)},
                )
            )

    def resolve_conflict(self, payload: dict[str, Any]) -> dict[str, Any]:
        return _run_mutation(
            self._session,
            payload,
            lambda p: self._conflicts.resolve_conflict(ResolveConflictRequest.model_validate(p)),
        )

    def dismiss_conflict(self, payload: dict[str, Any]) -> dict[str, Any]:
        return _run_mutation(
            self._session,
            payload,
            lambda p: self._conflicts.dismiss_conflict(DismissConflictRequest.model_validate(p)),
        )

    def assert_batch(self, payload: dict[str, Any]) -> dict[str, Any]:
        return _run_mutation(
            self._session,
            payload,
            lambda p: BatchService(self._session).assert_batch(
                AssertBatchRequest.model_validate(p)
            ),
        )


class OntologyMCPTools:
    """Translate MCP tool calls into ontology read and proposal operations."""

    def __init__(self, session: Session) -> None:
        self._session = session
        self._ontology = OntologyService(session)
        self._proposals = ProposalService(session)

    def get_class(
        self,
        *,
        class_key: str | None = None,
        class_id: str | None = None,
        alias: str | None = None,
        namespace_key: str = "core",
    ) -> dict[str, Any]:
        try:
            result: OntologyClassResponse = self._ontology.get_class(
                class_key=class_key,
                class_id=None if class_id is None else uuid.UUID(class_id),
                alias=alias,
                namespace_key=namespace_key,
            )
            return result.model_dump(mode="json")
        except (DomainError, ValueError) as exc:
            if isinstance(exc, DomainError):
                return _error(exc)
            return _error(
                ValidationFailedError("Invalid class lookup", details={"error": str(exc)})
            )

    def get_predicate(
        self,
        *,
        predicate_key: str | None = None,
        predicate_id: str | None = None,
        alias: str | None = None,
        namespace_key: str = "core",
    ) -> dict[str, Any]:
        try:
            result: OntologyPredicateResponse = self._ontology.get_predicate(
                predicate_key=predicate_key,
                predicate_id=None if predicate_id is None else uuid.UUID(predicate_id),
                alias=alias,
                namespace_key=namespace_key,
            )
            return result.model_dump(mode="json")
        except (DomainError, ValueError) as exc:
            if isinstance(exc, DomainError):
                return _error(exc)
            return _error(
                ValidationFailedError("Invalid predicate lookup", details={"error": str(exc)})
            )

    def search_ontology(
        self,
        *,
        query: str,
        namespace_key: str | None = "core",
        limit: int = 25,
    ) -> dict[str, Any]:
        try:
            result: OntologySearchResponse = self._ontology.search_ontology(
                query=query,
                namespace_key=namespace_key,
                limit=limit,
            )
            return result.model_dump(mode="json")
        except DomainError as exc:
            return _error(exc)

    def get_ontology_context(
        self,
        *,
        class_key: str | None = None,
        class_id: str | None = None,
        alias: str | None = None,
        namespace_key: str = "core",
    ) -> dict[str, Any]:
        try:
            result: OntologyContextResponse = self._ontology.get_ontology_context(
                class_key=class_key,
                class_id=None if class_id is None else uuid.UUID(class_id),
                alias=alias,
                namespace_key=namespace_key,
            )
            return result.model_dump(mode="json")
        except (DomainError, ValueError) as exc:
            if isinstance(exc, DomainError):
                return _error(exc)
            return _error(
                ValidationFailedError(
                    "Invalid ontology context lookup", details={"error": str(exc)}
                )
            )

    def get_proposal(self, proposal_id: str) -> dict[str, Any]:
        try:
            result: ProposalResponse = self._proposals.get_proposal(uuid.UUID(proposal_id))
            return result.model_dump(mode="json")
        except (DomainError, ValueError) as exc:
            if isinstance(exc, DomainError):
                return _error(exc)
            return _error(
                ValidationFailedError(
                    "Invalid proposal id",
                    details={"proposal_id": proposal_id, "error": str(exc)},
                )
            )

    def propose_class(self, payload: dict[str, Any]) -> dict[str, Any]:
        return _run_mutation(
            self._session,
            payload,
            lambda p: self._proposals.propose_class(ProposeClassRequest.model_validate(p)),
        )

    def propose_predicate(self, payload: dict[str, Any]) -> dict[str, Any]:
        return _run_mutation(
            self._session,
            payload,
            lambda p: self._proposals.propose_predicate(ProposePredicateRequest.model_validate(p)),
        )

    def propose_constraint(self, payload: dict[str, Any]) -> dict[str, Any]:
        return _run_mutation(
            self._session,
            payload,
            lambda p: self._proposals.propose_constraint(
                ProposeConstraintRequest.model_validate(p)
            ),
        )

    def propose_alias(self, payload: dict[str, Any]) -> dict[str, Any]:
        return _run_mutation(
            self._session,
            payload,
            lambda p: self._proposals.propose_alias(ProposeAliasRequest.model_validate(p)),
        )

    def propose_class_parent(self, payload: dict[str, Any]) -> dict[str, Any]:
        return _run_mutation(
            self._session,
            payload,
            lambda p: self._proposals.propose_class_parent(
                ProposeClassParentRequest.model_validate(p)
            ),
        )

    def apply_ontology_proposal(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Commit a READY_TO_APPLY / applyable proposal into the live ontology.

        Revalidates gates and live ontology state; does not bypass governance.
        """
        return _run_mutation(
            self._session,
            payload,
            lambda p: self._proposals.apply_proposal(ApplyProposalRequest.model_validate(p)),
        )

    def challenge_ontology_review(self, payload: dict[str, Any]) -> dict[str, Any]:
        from semantic_memory.schemas.semantic_review import ChallengeOntologyReviewRequest

        return _run_mutation(
            self._session,
            payload,
            lambda p: self._proposals.challenge_ontology_review(
                ChallengeOntologyReviewRequest.model_validate(p)
            ),
        )

    def answer_semantic_clarification(self, payload: dict[str, Any]) -> dict[str, Any]:
        from semantic_memory.schemas.semantic_review import AnswerSemanticClarificationRequest

        return _run_mutation(
            self._session,
            payload,
            lambda p: self._proposals.answer_semantic_clarification(
                AnswerSemanticClarificationRequest.model_validate(p)
            ),
        )


class FeedbackMCPTools:
    """Translate MCP tool calls into feedback service operations."""

    def __init__(self, session: Session) -> None:
        self._session = session
        self._feedback = FeedbackService(session)

    def report_feedback(self, payload: dict[str, Any]) -> dict[str, Any]:
        return _run_mutation(
            self._session,
            payload,
            lambda p: self._feedback.report_feedback(ReportFeedbackRequest.model_validate(p)),
        )
