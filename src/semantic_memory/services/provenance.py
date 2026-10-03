"""Source deduplication and statement evidence services."""

from __future__ import annotations

import uuid

from sqlalchemy.orm import Session

from semantic_memory.exceptions import (
    UnknownSourceError,
    UnknownStatementError,
    ValidationFailedError,
)
from semantic_memory.models import Source, StatementEvidence, StatementStatus
from semantic_memory.models.capabilities import Capability
from semantic_memory.repositories.provenance import ProvenanceRepository, hash_source_content
from semantic_memory.repositories.statements import StatementRepository
from semantic_memory.schemas.provenance import (
    AddEvidenceRequest,
    AddEvidenceResponse,
    EnsureSourceRequest,
    EnsureSourceResponse,
    EvidenceResponse,
    ExplainStatementResponse,
    SourceInput,
    SourceOutcome,
    SourceResponse,
)
from semantic_memory.services.actors import ActorService
from semantic_memory.services.mutations import MutationRunner
from semantic_memory.validation.literals import normalize_confidence, normalize_optional_to_utc


class ProvenanceService:
    def __init__(self, session: Session) -> None:
        self._session = session
        self._actors = ActorService(session)
        self._provenance = ProvenanceRepository(session)
        self._statements = StatementRepository(session)
        self._mutations = MutationRunner(session)

    def ensure_source(self, request: EnsureSourceRequest) -> EnsureSourceResponse:
        actor = self._actors.require_active_actor(request.actor_key)
        self._actors.require_capability(actor, Capability.KNOWLEDGE_WRITE)
        return self._mutations.run(
            actor=actor,
            operation_name="ensure_source",
            request=request,
            response_model=EnsureSourceResponse,
            constraint_name="source_write",
            execute=lambda: self._ensure_source_body(request=request, actor_id=actor.id),
        )

    def add_evidence(self, request: AddEvidenceRequest) -> AddEvidenceResponse:
        actor = self._actors.require_active_actor(request.actor_key)
        self._actors.require_capability(actor, Capability.KNOWLEDGE_WRITE)
        return self._mutations.run(
            actor=actor,
            operation_name="add_evidence",
            request=request,
            response_model=AddEvidenceResponse,
            constraint_name="evidence_write",
            execute=lambda: self._add_evidence_body(request=request, actor_id=actor.id),
        )

    def explain_statement(self, statement_id: uuid.UUID) -> ExplainStatementResponse:
        statement = self._statements.get(statement_id)
        if statement is None:
            raise UnknownStatementError(
                f"Statement {statement_id} was not found",
                details={"statement_id": str(statement_id)},
            )
        evidence_rows = self._provenance.list_evidence_for_statement(statement_id)
        return ExplainStatementResponse(
            statement_id=statement.id,
            asserted_at=statement.asserted_at,
            observed_at=statement.observed_at,
            valid_from=statement.valid_from,
            valid_to=statement.valid_to,
            status=statement.status,
            evidence=[self._to_evidence_response(row) for row in evidence_rows],
        )

    def _ensure_source_body(
        self, *, request: EnsureSourceRequest, actor_id: uuid.UUID
    ) -> EnsureSourceResponse:
        source, created = self._resolve_or_create_source(request, actor_id=actor_id)
        return EnsureSourceResponse(
            outcome=SourceOutcome.CREATE if created else SourceOutcome.REUSE,
            source=self._to_source_response(source),
            request_id=request.request_id,
            reused=not created,
        )

    def _add_evidence_body(
        self, *, request: AddEvidenceRequest, actor_id: uuid.UUID
    ) -> AddEvidenceResponse:
        statement = self._statements.get(request.statement_id)
        if statement is None:
            raise UnknownStatementError(
                f"Statement {request.statement_id} was not found",
                details={"statement_id": str(request.statement_id)},
                request_id=str(request.request_id),
            )
        if statement.status == StatementStatus.RETRACTED.value:
            # Evidence may still be attached for audit of retracted claims.
            pass

        try:
            confidence = normalize_confidence(request.extraction_confidence)
        except ValueError as exc:
            raise ValidationFailedError(
                str(exc),
                details={"extraction_confidence": str(request.extraction_confidence)},
                request_id=str(request.request_id),
            ) from exc

        source, _created = self._resolve_or_create_source(request.source, actor_id=actor_id)
        excerpt = request.excerpt.strip() if request.excerpt else None
        locator = request.locator.strip() if request.locator else None
        existing = self._provenance.find_evidence(
            statement_id=statement.id,
            source_id=source.id,
            excerpt=excerpt,
            locator=locator,
        )
        if existing is not None:
            return AddEvidenceResponse(
                outcome=SourceOutcome.REUSE,
                evidence=self._to_evidence_response(existing),
                request_id=request.request_id,
                reused=True,
            )

        evidence = self._provenance.create_evidence(
            statement_id=statement.id,
            source_id=source.id,
            asserted_by_actor_id=actor_id,
            excerpt=excerpt,
            locator=locator,
            extraction_confidence=confidence,
        )
        return AddEvidenceResponse(
            outcome=SourceOutcome.CREATE,
            evidence=self._to_evidence_response(evidence),
            request_id=request.request_id,
            reused=False,
        )

    def _resolve_or_create_source(
        self, source_input: SourceInput, *, actor_id: uuid.UUID
    ) -> tuple[Source, bool]:
        if source_input.source_id is not None:
            existing = self._provenance.get_source(source_input.source_id)
            if existing is None:
                raise UnknownSourceError(
                    f"Source {source_input.source_id} was not found",
                    details={"source_id": str(source_input.source_id)},
                )
            return existing, False

        content_hash = source_input.content_hash or hash_source_content(
            source_input.source_system,
            source_input.external_id,
            source_input.uri,
            source_input.title,
        )
        # Take every applicable identity lock so concurrent callers that share
        # either external identity or content_hash serialize before lookup/create.
        lock_materials: list[str] = []
        if source_input.source_system and source_input.external_id:
            lock_materials.append(f"ext:{source_input.source_system}:{source_input.external_id}")
        if content_hash:
            lock_materials.append(f"hash:{content_hash}")
        if not lock_materials:
            lock_materials.append(f"hash:{uuid.uuid4()}")
        for lock_material in sorted(lock_materials):
            self._provenance.acquire_source_lock(material=lock_material)

        if source_input.source_system and source_input.external_id:
            existing = self._provenance.find_source_by_external(
                source_system=source_input.source_system,
                external_id=source_input.external_id,
            )
            if existing is not None:
                return existing, False
        if content_hash:
            existing = self._provenance.find_source_by_content_hash(content_hash)
            if existing is not None:
                return existing, False

        try:
            reliability = normalize_confidence(source_input.reliability)
            retrieved_at = normalize_optional_to_utc(source_input.retrieved_at)
        except ValueError as exc:
            raise ValidationFailedError(str(exc), details={"source": "invalid fields"}) from exc

        created = self._provenance.create_source(
            created_by_actor_id=actor_id,
            source_system=source_input.source_system,
            external_id=source_input.external_id,
            uri=source_input.uri,
            title=source_input.title,
            content_hash=content_hash,
            reliability=reliability,
            retrieved_at=retrieved_at,
            metadata_json=source_input.metadata,
        )
        return created, True

    def _to_source_response(self, source: Source) -> SourceResponse:
        return SourceResponse(
            id=source.id,
            source_system=source.source_system,
            external_id=source.external_id,
            uri=source.uri,
            title=source.title,
            content_hash=source.content_hash,
            reliability=source.reliability,
            retrieved_at=source.retrieved_at,
            metadata=source.metadata_json,
            created_at=source.created_at,
            updated_at=source.updated_at,
        )

    def _to_evidence_response(self, evidence: StatementEvidence) -> EvidenceResponse:
        source = self._provenance.get_source(evidence.source_id)
        if source is None:
            raise UnknownSourceError(
                f"Source {evidence.source_id} was not found",
                details={"source_id": str(evidence.source_id)},
            )
        return EvidenceResponse(
            id=evidence.id,
            statement_id=evidence.statement_id,
            source=self._to_source_response(source),
            excerpt=evidence.excerpt,
            locator=evidence.locator,
            extraction_confidence=evidence.extraction_confidence,
            asserted_by_actor_id=evidence.asserted_by_actor_id,
            created_at=evidence.created_at,
        )
