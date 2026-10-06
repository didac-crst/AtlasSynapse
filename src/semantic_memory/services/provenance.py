"""Source deduplication, content revisions, and statement evidence services."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from semantic_memory.content.canonicalize import CanonicalizationResult, canonicalize_content
from semantic_memory.exceptions import (
    UnknownEntityError,
    UnknownSourceError,
    UnknownStatementError,
    ValidationFailedError,
)
from semantic_memory.models import Source, SourceContentRevision, StatementEvidence, StatementStatus
from semantic_memory.models.capabilities import Capability
from semantic_memory.repositories.entities import EntityRepository
from semantic_memory.repositories.provenance import (
    ProvenanceRepository,
    hash_source_content,
    revision_locator,
)
from semantic_memory.repositories.statements import StatementRepository
from semantic_memory.schemas.provenance import (
    AddEvidenceRequest,
    AddEvidenceResponse,
    EnsureSourceRequest,
    EnsureSourceResponse,
    EvidenceResponse,
    ExplainStatementResponse,
    GetSourceContentRequest,
    GetSourceContentResponse,
    IngestSourceContentRequest,
    IngestSourceContentResponse,
    SearchSourceContentRequest,
    SearchSourceContentResponse,
    SourceContentPassage,
    SourceContentRevisionResponse,
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
        self._entities = EntityRepository(session)
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

    def ingest_source_content(
        self, request: IngestSourceContentRequest
    ) -> IngestSourceContentResponse:
        actor = self._actors.require_active_actor(request.actor_key)
        self._actors.require_capability(actor, Capability.KNOWLEDGE_WRITE)
        return self._mutations.run(
            actor=actor,
            operation_name="ingest_source_content",
            request=request,
            response_model=IngestSourceContentResponse,
            constraint_name="source_content_write",
            execute=lambda: self._ingest_source_content_body(
                request=request, actor_id=actor.id
            ),
        )

    def get_source_content(self, request: GetSourceContentRequest) -> GetSourceContentResponse:
        revision, source = self._resolve_revision(request)
        revision_response = self._to_revision_response(
            revision, include_original=request.include_original
        )
        return GetSourceContentResponse(
            source=self._to_source_response(source),
            revision=revision_response,
        )

    def search_source_content(
        self, request: SearchSourceContentRequest
    ) -> SearchSourceContentResponse:
        query = request.query.strip()
        if not query:
            raise ValidationFailedError("query must not be empty", details={"query": query})

        rows = self._provenance.search_content_revisions(
            query=query,
            source_id=request.source_id,
            entity_id=request.document_entity_id,
            limit=request.limit,
        )
        passages: list[SourceContentPassage] = []
        needle = query.casefold()
        for revision, source in rows:
            body = revision.canonical_content
            lower = body.casefold()
            start = lower.find(needle)
            if start < 0:
                continue
            end = start + len(query)
            left = max(0, start - request.context_chars)
            right = min(len(body), end + request.context_chars)
            excerpt = body[left:right]
            passages.append(
                SourceContentPassage(
                    source=self._to_source_response(source),
                    revision_id=revision.id,
                    revision_number=revision.revision_number,
                    excerpt=excerpt,
                    start_offset=start,
                    end_offset=end,
                    locator=revision_locator(revision.id, start=start, end=end),
                )
            )
        return SearchSourceContentResponse(query=query, passages=passages)

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

    def _ingest_source_content_body(
        self, *, request: IngestSourceContentRequest, actor_id: uuid.UUID
    ) -> IngestSourceContentResponse:
        document_entity_id = request.document_entity_id or request.source.entity_id
        if document_entity_id is not None:
            entity = self._entities.get(document_entity_id)
            if entity is None:
                raise UnknownEntityError(
                    f"Entity {document_entity_id} was not found",
                    details={"document_entity_id": str(document_entity_id)},
                    request_id=str(request.request_id),
                )

        source_input = request.source
        if document_entity_id is not None and source_input.entity_id is None:
            source_input = source_input.model_copy(update={"entity_id": document_entity_id})

        source, _source_created = self._resolve_or_create_source(
            source_input, actor_id=actor_id
        )
        if document_entity_id is not None and source.entity_id != document_entity_id:
            source = self._provenance.set_source_entity_id(source, document_entity_id)

        # Access/retrieval policy and approximate dates live on source metadata.
        source_meta_keys = ("visibility", "published_at", "date_precision")
        source_meta = {
            key: request.metadata[key]
            for key in source_meta_keys
            if key in request.metadata
        }
        if source_meta:
            source = self._provenance.merge_source_metadata(source, source_meta)

        try:
            canonicalized = self._canonicalize_ingest(request)
        except ValueError as exc:
            raise ValidationFailedError(
                str(exc),
                details={"content_format": request.content_format},
                request_id=str(request.request_id),
            ) from exc

        canonical_hash = canonicalized.canonical_hash
        self._provenance.acquire_source_lock(
            material=f"content:{source.id}:{canonical_hash}"
        )
        existing = self._provenance.find_content_revision_by_hash(
            source_id=source.id,
            canonical_content_hash=canonical_hash,
        )
        if existing is not None:
            return IngestSourceContentResponse(
                outcome=SourceOutcome.REUSE,
                source=self._to_source_response(source),
                revision=self._to_revision_response(existing),
                request_id=request.request_id,
                reused=True,
            )

        try:
            captured_at = normalize_optional_to_utc(request.captured_at) or datetime.now(UTC)
        except ValueError as exc:
            raise ValidationFailedError(
                str(exc),
                details={"captured_at": str(request.captured_at)},
                request_id=str(request.request_id),
            ) from exc

        revision_metadata = dict(request.metadata)
        revision_metadata["canonicalization"] = canonicalized.metadata()

        revision = self._provenance.create_content_revision(
            source_id=source.id,
            revision_number=self._provenance.next_revision_number(source.id),
            canonical_content=canonicalized.canonical_content,
            canonical_format=canonicalized.canonical_format,
            canonical_content_hash=canonical_hash,
            original_content=canonicalized.original_content,
            original_format=canonicalized.original_format,
            original_content_hash=canonicalized.original_hash,
            captured_at=captured_at,
            created_by_actor_id=actor_id,
            metadata_json=revision_metadata,
        )
        # Keep legacy source.content_hash aligned with latest canonical body when set.
        source.content_hash = canonical_hash
        self._session.flush()

        return IngestSourceContentResponse(
            outcome=SourceOutcome.CREATE,
            source=self._to_source_response(source),
            revision=self._to_revision_response(revision),
            request_id=request.request_id,
            reused=False,
        )

    def _canonicalize_ingest(
        self, request: IngestSourceContentRequest
    ) -> CanonicalizationResult:
        """Resolve request fields into a CanonicalizationResult."""
        if request.content is not None and request.content_format is not None:
            return canonicalize_content(
                request.content,
                request.content_format,
                request.canonical_format,
            )

        # Legacy: original provided → canonicalize from original (backend owns MD).
        if request.original_content is not None and request.original_format is not None:
            return canonicalize_content(
                request.original_content,
                request.original_format,
                request.canonical_format,
            )

        # Legacy: caller-supplied body treated as already in target format.
        assert request.canonical_content is not None
        return canonicalize_content(
            request.canonical_content,
            request.canonical_format,
            request.canonical_format,
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

    def _resolve_revision(
        self, request: GetSourceContentRequest
    ) -> tuple[SourceContentRevision, Source]:
        if request.revision_id is not None:
            revision = self._provenance.get_content_revision(request.revision_id)
            if revision is None:
                raise UnknownSourceError(
                    f"Content revision {request.revision_id} was not found",
                    details={"revision_id": str(request.revision_id)},
                )
            source = self._provenance.get_source(revision.source_id)
            if source is None:
                raise UnknownSourceError(
                    f"Source {revision.source_id} was not found",
                    details={"source_id": str(revision.source_id)},
                )
            if request.source_id is not None and source.id != request.source_id:
                raise ValidationFailedError(
                    "revision_id does not belong to source_id",
                    details={
                        "revision_id": str(request.revision_id),
                        "source_id": str(request.source_id),
                    },
                )
            if (
                request.document_entity_id is not None
                and source.entity_id != request.document_entity_id
            ):
                raise ValidationFailedError(
                    "revision_id does not belong to document_entity_id",
                    details={
                        "revision_id": str(request.revision_id),
                        "document_entity_id": str(request.document_entity_id),
                    },
                )
            return revision, source

        if request.source_id is not None:
            source = self._provenance.get_source(request.source_id)
            if source is None:
                raise UnknownSourceError(
                    f"Source {request.source_id} was not found",
                    details={"source_id": str(request.source_id)},
                )
            revision = self._provenance.latest_content_revision(source.id)
            if revision is None:
                raise UnknownSourceError(
                    f"Source {source.id} has no content revisions",
                    details={"source_id": str(source.id)},
                )
            return revision, source

        assert request.document_entity_id is not None
        sources = self._provenance.list_sources_for_entity(request.document_entity_id)
        if not sources:
            raise UnknownSourceError(
                f"No sources linked to entity {request.document_entity_id}",
                details={"document_entity_id": str(request.document_entity_id)},
            )
        # Prefer the most recently updated linked source that has content.
        for source in reversed(sources):
            revision = self._provenance.latest_content_revision(source.id)
            if revision is not None:
                return revision, source
        raise UnknownSourceError(
            f"No content revisions for entity {request.document_entity_id}",
            details={"document_entity_id": str(request.document_entity_id)},
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

        if source_input.entity_id is not None:
            entity = self._entities.get(source_input.entity_id)
            if entity is None:
                raise UnknownEntityError(
                    f"Entity {source_input.entity_id} was not found",
                    details={"entity_id": str(source_input.entity_id)},
                )

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
            entity_id=source_input.entity_id,
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
            entity_id=source.entity_id,
            metadata=source.metadata_json,
            created_at=source.created_at,
            updated_at=source.updated_at,
        )

    def _to_revision_response(
        self,
        revision: SourceContentRevision,
        *,
        include_original: bool = True,
    ) -> SourceContentRevisionResponse:
        return SourceContentRevisionResponse(
            id=revision.id,
            source_id=revision.source_id,
            revision_number=revision.revision_number,
            canonical_content=revision.canonical_content,
            canonical_format=revision.canonical_format,  # type: ignore[arg-type]
            canonical_content_hash=revision.canonical_content_hash,
            original_content=revision.original_content if include_original else None,
            original_format=(
                revision.original_format if include_original else None  # type: ignore[arg-type]
            ),
            original_content_hash=(
                revision.original_content_hash if include_original else None
            ),
            captured_at=revision.captured_at,
            metadata=revision.metadata_json,
            created_at=revision.created_at,
            created_by_actor_id=revision.created_by_actor_id,
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
