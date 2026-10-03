"""Atomic batch ingestion with preload and explicit result buckets."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from semantic_memory.exceptions import (
    DomainError,
    UnknownClassError,
    UnknownEntityError,
    UnknownPredicateError,
    ValidationFailedError,
)
from semantic_memory.models import Entity, Statement
from semantic_memory.models.capabilities import Capability
from semantic_memory.models.enums import BatchStatus, EntityStatus
from semantic_memory.repositories.batches import BatchRepository
from semantic_memory.repositories.entities import EntityRepository
from semantic_memory.repositories.ontology import OntologyRepository
from semantic_memory.repositories.statements import StatementRepository
from semantic_memory.schemas.batches import (
    AssertBatchRequest,
    AssertBatchResponse,
    BatchItemOutcome,
    BatchItemResult,
)
from semantic_memory.schemas.entities import CreateEntityRequest, ResolutionOutcome
from semantic_memory.schemas.statements import AssertionOutcome, AssertStatementRequest
from semantic_memory.services.actors import ActorService
from semantic_memory.services.entities import EntityService
from semantic_memory.services.mutations import MutationRunner
from semantic_memory.services.statements import StatementService


class _BatchAtomicAbort(Exception):
    """Roll back item writes while allowing a structured batch response."""


@dataclass
class _BatchPreload:
    entities: dict[uuid.UUID, Entity] = field(default_factory=dict)
    class_keys: set[str] = field(default_factory=set)
    predicate_keys: set[str] = field(default_factory=set)
    active_statements: dict[tuple[uuid.UUID, uuid.UUID], list[Statement]] = field(
        default_factory=dict
    )


class BatchService:
    def __init__(self, session: Session) -> None:
        self._session = session
        self._actors = ActorService(session)
        self._batches = BatchRepository(session)
        self._entities = EntityRepository(session)
        self._ontology = OntologyRepository(session)
        self._statements = StatementRepository(session)
        self._entity_service = EntityService(session)
        self._statement_service = StatementService(session)
        self._mutations = MutationRunner(session)

    def assert_batch(self, request: AssertBatchRequest) -> AssertBatchResponse:
        actor = self._actors.require_active_actor(request.actor_key)
        self._actors.require_capability(actor, Capability.KNOWLEDGE_WRITE)
        return self._mutations.run(
            actor=actor,
            operation_name="assert_batch",
            request=request,
            response_model=AssertBatchResponse,
            constraint_name="batch_write",
            execute=lambda: self._assert_batch_body(request=request, actor_id=actor.id),
        )

    def _assert_batch_body(
        self, *, request: AssertBatchRequest, actor_id: uuid.UUID
    ) -> AssertBatchResponse:
        item_count = len(request.entities) + len(request.statements)
        batch = self._batches.create(
            actor_id=actor_id,
            request_id=request.request_id,
            item_count=item_count,
            status=BatchStatus.RUNNING,
        )
        preload = self._preload(request)

        created: list[BatchItemResult] = []
        reused: list[BatchItemResult] = []
        ambiguous: list[BatchItemResult] = []
        rejected: list[BatchItemResult] = []
        ontology_required: list[BatchItemResult] = []
        client_entity_ids: dict[str, uuid.UUID] = {}

        try:
            with self._session.begin_nested():
                for entity_item in request.entities:
                    entity_request = CreateEntityRequest(
                        actor_key=request.actor_key,
                        request_id=request.request_id,
                        idempotency_key=(
                            f"{request.idempotency_key}:entity:{entity_item.client_item_id}"
                        ),
                        trace_id=request.trace_id,
                        canonical_name=entity_item.canonical_name,
                        class_key=entity_item.class_key,
                        namespace_key=entity_item.namespace_key,
                        aliases=entity_item.aliases,
                        external_reference=entity_item.external_reference,
                    )
                    try:
                        entity_result = self._entity_service._create_entity_body(
                            request=entity_request,
                            actor_id=actor_id,
                        )
                    except UnknownClassError as exc:
                        ontology_required.append(
                            BatchItemResult(
                                client_item_id=entity_item.client_item_id,
                                outcome=BatchItemOutcome.ONTOLOGY_REQUIRED,
                                error_code=exc.error_code,
                                message=exc.message,
                                details=exc.details,
                            )
                        )
                        continue
                    except DomainError as exc:
                        rejected.append(
                            BatchItemResult(
                                client_item_id=entity_item.client_item_id,
                                outcome=BatchItemOutcome.REJECTED,
                                error_code=exc.error_code,
                                message=exc.message,
                                details=exc.details,
                            )
                        )
                        continue

                    if entity_result.outcome == ResolutionOutcome.AMBIGUOUS:
                        ambiguous.append(
                            BatchItemResult(
                                client_item_id=entity_item.client_item_id,
                                outcome=BatchItemOutcome.AMBIGUOUS,
                                entity=entity_result,
                                candidates=entity_result.candidates,
                            )
                        )
                        continue
                    if entity_result.entity is not None:
                        client_entity_ids[entity_item.client_item_id] = entity_result.entity.id
                        loaded = self._entities.get(entity_result.entity.id)
                        if loaded is not None:
                            preload.entities[entity_result.entity.id] = loaded
                    entity_bucket = (
                        reused if entity_result.outcome == ResolutionOutcome.REUSE else created
                    )
                    entity_bucket.append(
                        BatchItemResult(
                            client_item_id=entity_item.client_item_id,
                            outcome=(
                                BatchItemOutcome.REUSED
                                if entity_result.outcome == ResolutionOutcome.REUSE
                                else BatchItemOutcome.CREATED
                            ),
                            entity=entity_result,
                        )
                    )

                for statement_item in request.statements:
                    try:
                        subject_id = self._resolve_ref(
                            entity_id=statement_item.subject_entity_id,
                            client_item_id=statement_item.subject_client_item_id,
                            client_entity_ids=client_entity_ids,
                            preload=preload,
                            field_name="subject",
                        )
                        object_entity_id = statement_item.object_entity_id
                        if statement_item.object_client_item_id is not None:
                            object_entity_id = self._resolve_ref(
                                entity_id=None,
                                client_item_id=statement_item.object_client_item_id,
                                client_entity_ids=client_entity_ids,
                                preload=preload,
                                field_name="object",
                            )
                    except DomainError as exc:
                        rejected.append(
                            BatchItemResult(
                                client_item_id=statement_item.client_item_id,
                                outcome=BatchItemOutcome.REJECTED,
                                error_code=exc.error_code,
                                message=exc.message,
                                details=exc.details,
                            )
                        )
                        continue

                    statement_request = AssertStatementRequest(
                        actor_key=request.actor_key,
                        request_id=request.request_id,
                        idempotency_key=(
                            f"{request.idempotency_key}:statement:{statement_item.client_item_id}"
                        ),
                        trace_id=request.trace_id,
                        subject_entity_id=subject_id,
                        predicate_key=statement_item.predicate_key,
                        namespace_key=statement_item.namespace_key,
                        object_entity_id=object_entity_id,
                        object_string=statement_item.object_string,
                        object_number=statement_item.object_number,
                        object_boolean=statement_item.object_boolean,
                        object_datetime=statement_item.object_datetime,
                        object_json=statement_item.object_json,
                        observed_at=statement_item.observed_at,
                        valid_from=statement_item.valid_from,
                        valid_to=statement_item.valid_to,
                        confidence=statement_item.confidence,
                    )
                    try:
                        statement_result = self._statement_service._assert_new(
                            request=statement_request,
                            actor_id=actor_id,
                        )
                    except (UnknownPredicateError, UnknownClassError) as exc:
                        ontology_required.append(
                            BatchItemResult(
                                client_item_id=statement_item.client_item_id,
                                outcome=BatchItemOutcome.ONTOLOGY_REQUIRED,
                                error_code=exc.error_code,
                                message=exc.message,
                                details=exc.details,
                            )
                        )
                        continue
                    except DomainError as exc:
                        rejected.append(
                            BatchItemResult(
                                client_item_id=statement_item.client_item_id,
                                outcome=BatchItemOutcome.REJECTED,
                                error_code=exc.error_code,
                                message=exc.message,
                                details=exc.details,
                            )
                        )
                        continue

                    statement_bucket = (
                        reused if statement_result.outcome == AssertionOutcome.REUSE else created
                    )
                    statement_bucket.append(
                        BatchItemResult(
                            client_item_id=statement_item.client_item_id,
                            outcome=(
                                BatchItemOutcome.REUSED
                                if statement_result.outcome == AssertionOutcome.REUSE
                                else BatchItemOutcome.CREATED
                            ),
                            statement=statement_result,
                            statement_ref=statement_result.statement,
                        )
                    )

                if rejected or ontology_required or ambiguous:
                    raise _BatchAtomicAbort()
        except _BatchAtomicAbort:
            self._batches.set_status(
                batch,
                status=BatchStatus.FAILED,
                metadata_json={
                    "atomic": True,
                    "rejected": len(rejected),
                    "ontology_required": len(ontology_required),
                    "ambiguous": len(ambiguous),
                },
            )
            return AssertBatchResponse(
                batch_id=batch.id,
                status=BatchStatus.FAILED,
                created=[],
                reused=[],
                ambiguous=ambiguous,
                rejected=rejected,
                ontology_required=ontology_required,
                request_id=request.request_id,
            )

        self._batches.set_status(
            batch,
            status=BatchStatus.SUCCEEDED,
            metadata_json={
                "atomic": True,
                "created": len(created),
                "reused": len(reused),
            },
        )
        return AssertBatchResponse(
            batch_id=batch.id,
            status=BatchStatus.SUCCEEDED,
            created=created,
            reused=reused,
            ambiguous=[],
            rejected=[],
            ontology_required=[],
            request_id=request.request_id,
        )

    def _preload(self, request: AssertBatchRequest) -> _BatchPreload:
        preload = _BatchPreload()
        entity_ids = {
            item.subject_entity_id
            for item in request.statements
            if item.subject_entity_id is not None
        }
        entity_ids.update(
            item.object_entity_id
            for item in request.statements
            if item.object_entity_id is not None
        )
        for entity_id in entity_ids:
            entity = self._entities.get(entity_id)
            if entity is not None and entity.status == EntityStatus.ACTIVE.value:
                preload.entities[entity_id] = entity

        for entity_item in request.entities:
            ontology_class = self._ontology.get_class_by_key(
                namespace_key=entity_item.namespace_key,
                class_key=entity_item.class_key,
            )
            if ontology_class is not None:
                preload.class_keys.add(f"{entity_item.namespace_key}:{entity_item.class_key}")

        predicate_ids: list[uuid.UUID] = []
        for statement_item in request.statements:
            predicate = self._ontology.get_predicate_by_key(
                namespace_key=statement_item.namespace_key,
                predicate_key=statement_item.predicate_key,
            )
            if predicate is None:
                continue
            preload.predicate_keys.add(
                f"{statement_item.namespace_key}:{statement_item.predicate_key}"
            )
            predicate_ids.append(predicate.id)

        for entity_id in preload.entities:
            for predicate_id in predicate_ids:
                rows = self._statements.find_asserted_for_predicate(
                    subject_entity_id=entity_id,
                    predicate_id=predicate_id,
                )
                if rows:
                    preload.active_statements[(entity_id, predicate_id)] = rows
        return preload

    def _resolve_ref(
        self,
        *,
        entity_id: uuid.UUID | None,
        client_item_id: str | None,
        client_entity_ids: dict[str, uuid.UUID],
        preload: _BatchPreload,
        field_name: str,
    ) -> uuid.UUID:
        if entity_id is not None:
            if entity_id not in preload.entities:
                raise UnknownEntityError(
                    f"{field_name} entity {entity_id} was not found or is inactive",
                    details={f"{field_name}_entity_id": str(entity_id)},
                )
            return entity_id
        assert client_item_id is not None
        resolved = client_entity_ids.get(client_item_id)
        if resolved is None:
            raise ValidationFailedError(
                f"{field_name}_client_item_id '{client_item_id}' was not created in this batch",
                details={f"{field_name}_client_item_id": client_item_id},
            )
        return resolved
