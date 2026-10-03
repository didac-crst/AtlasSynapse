"""Statement assertion application service."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from semantic_memory.exceptions import (
    CardinalityViolationError,
    DbConstraintError,
    DomainViolationError,
    IdempotencyKeyReusedError,
    InternalError,
    InvalidLiteralTypeError,
    RangeViolationError,
    UnknownEntityError,
    UnknownPredicateError,
    UnknownStatementError,
    ValidationFailedError,
)
from semantic_memory.models import EntityStatus, Statement
from semantic_memory.models.capabilities import Capability
from semantic_memory.models.enums import Cardinality, StatementStatus, ValueKind
from semantic_memory.repositories.entities import EntityRepository
from semantic_memory.repositories.idempotency import IdempotencyRepository, hash_request_payload
from semantic_memory.repositories.ontology import OntologyRepository
from semantic_memory.repositories.statements import StatementRepository
from semantic_memory.schemas.statements import (
    AssertionOutcome,
    AssertStatementRequest,
    AssertStatementResponse,
    StatementResponse,
)
from semantic_memory.services.actors import ActorService
from semantic_memory.validation.literals import normalize_confidence, normalize_object_identity


class StatementService:
    def __init__(self, session: Session) -> None:
        self._session = session
        self._actors = ActorService(session)
        self._entities = EntityRepository(session)
        self._ontology = OntologyRepository(session)
        self._statements = StatementRepository(session)
        self._idempotency = IdempotencyRepository(session)

    def get(self, statement_id: uuid.UUID) -> StatementResponse:
        statement = self._statements.get(statement_id)
        if statement is None:
            raise UnknownStatementError(
                f"Statement {statement_id} was not found",
                details={"statement_id": str(statement_id)},
            )
        return self._to_response(statement)

    def assert_statement(self, request: AssertStatementRequest) -> AssertStatementResponse:
        actor = self._actors.require_active_actor(request.actor_key)
        self._actors.require_capability(actor, Capability.KNOWLEDGE_WRITE)

        payload = request.model_dump(
            mode="json",
            exclude={"request_id", "trace_id", "idempotency_key"},
        )
        request_hash = hash_request_payload(payload)

        try:
            with self._session.begin_nested():
                return self._assert_body(
                    request=request,
                    actor_id=actor.id,
                    request_hash=request_hash,
                )
        except IntegrityError as exc:
            raise DbConstraintError(
                "Statement persistence violated a database constraint",
                details={"constraint": "statement_write"},
                request_id=str(request.request_id),
            ) from exc

    def _assert_body(
        self,
        *,
        request: AssertStatementRequest,
        actor_id: uuid.UUID,
        request_hash: str,
    ) -> AssertStatementResponse:
        reservation, created = self._idempotency.reserve_or_get(
            actor_id=actor_id,
            idempotency_key=request.idempotency_key,
            request_hash=request_hash,
            operation_name="assert_statement",
        )
        if reservation.request_hash != request_hash:
            raise IdempotencyKeyReusedError(
                "Idempotency key was reused with a different payload",
                details={
                    "idempotency_key": request.idempotency_key,
                    "actor_key": request.actor_key,
                },
                request_id=str(request.request_id),
            )
        if reservation.response_payload is not None:
            return AssertStatementResponse.model_validate(reservation.response_payload)
        if not created:
            raise InternalError(
                "Idempotency key is reserved by an in-flight request",
                details={"idempotency_key": request.idempotency_key},
                request_id=str(request.request_id),
            )

        response = self._assert_new(request=request, actor_id=actor_id)
        self._idempotency.store_response(reservation, response.model_dump(mode="json"))
        return response

    def _assert_new(
        self, *, request: AssertStatementRequest, actor_id: uuid.UUID
    ) -> AssertStatementResponse:
        self._validate_interval(request)
        try:
            confidence = normalize_confidence(request.confidence)
        except ValueError as exc:
            raise ValidationFailedError(
                str(exc),
                details={"confidence": str(request.confidence)},
                request_id=str(request.request_id),
            ) from exc

        subject = self._entities.get(request.subject_entity_id)
        if subject is None or subject.status != EntityStatus.ACTIVE.value:
            raise UnknownEntityError(
                f"Subject entity {request.subject_entity_id} was not found or is inactive",
                details={"subject_entity_id": str(request.subject_entity_id)},
                request_id=str(request.request_id),
            )

        predicate = self._ontology.get_predicate_by_key(
            namespace_key=request.namespace_key,
            predicate_key=request.predicate_key,
        )
        if predicate is None:
            raise UnknownPredicateError(
                f"Unknown predicate '{request.namespace_key}:{request.predicate_key}'",
                details={
                    "namespace_key": request.namespace_key,
                    "predicate_key": request.predicate_key,
                },
                request_id=str(request.request_id),
            )
        revision = self._ontology.get_current_predicate_revision(predicate)
        if revision is None:
            raise UnknownPredicateError(
                f"Predicate '{request.predicate_key}' has no current revision",
                details={"predicate_id": str(predicate.id)},
                request_id=str(request.request_id),
            )

        value_kind = ValueKind(revision.value_kind)
        self._validate_object_matches_kind(request, value_kind)
        self._validate_domain(request, revision.id, subject.id)
        if value_kind == ValueKind.ENTITY:
            self._validate_range(request, revision.id)

        try:
            normalized_object = normalize_object_identity(
                value_kind=value_kind,
                object_entity_id=request.object_entity_id,
                object_string=request.object_string,
                object_number=request.object_number,
                object_boolean=request.object_boolean,
                object_datetime=request.object_datetime,
                object_json=request.object_json,
            )
        except ValueError as exc:
            raise InvalidLiteralTypeError(
                str(exc),
                details={"value_kind": value_kind.value},
                request_id=str(request.request_id),
            ) from exc

        self._statements.acquire_assertion_lock(
            subject_entity_id=subject.id,
            predicate_id=predicate.id,
            normalized_object=normalized_object,
            valid_from=request.valid_from,
            valid_to=request.valid_to,
        )

        existing = self._statements.find_semantic_duplicate(
            subject_entity_id=subject.id,
            predicate_id=predicate.id,
            normalized_object=normalized_object,
            valid_from=request.valid_from,
            valid_to=request.valid_to,
        )
        if existing is not None:
            return AssertStatementResponse(
                outcome=AssertionOutcome.REUSE,
                statement=self._to_response(existing),
                request_id=request.request_id,
                reused=True,
            )

        if revision.cardinality == Cardinality.ONE.value:
            others = self._statements.find_asserted_for_predicate(
                subject_entity_id=subject.id,
                predicate_id=predicate.id,
            )
            if others:
                raise CardinalityViolationError(
                    f"Predicate '{request.predicate_key}' allows at most one asserted value",
                    details={
                        "predicate_key": request.predicate_key,
                        "subject_entity_id": str(subject.id),
                        "existing_statement_ids": [str(item.id) for item in others],
                    },
                    request_id=str(request.request_id),
                )

        statement = self._statements.create(
            subject_entity_id=subject.id,
            predicate_id=predicate.id,
            actor_id=actor_id,
            asserted_at=datetime.now(UTC),
            normalized_object=normalized_object,
            object_entity_id=request.object_entity_id,
            object_string=request.object_string,
            object_number=request.object_number,
            object_boolean=request.object_boolean,
            object_datetime=request.object_datetime,
            object_json=request.object_json,
            observed_at=request.observed_at,
            valid_from=request.valid_from,
            valid_to=request.valid_to,
            confidence=confidence,
        )
        return AssertStatementResponse(
            outcome=AssertionOutcome.CREATE,
            statement=self._to_response(statement),
            request_id=request.request_id,
            reused=False,
        )

    def _validate_interval(self, request: AssertStatementRequest) -> None:
        if (
            request.valid_from is not None
            and request.valid_to is not None
            and request.valid_to < request.valid_from
        ):
            raise ValidationFailedError(
                "valid_to must be greater than or equal to valid_from",
                details={
                    "valid_from": request.valid_from.isoformat(),
                    "valid_to": request.valid_to.isoformat(),
                },
                request_id=str(request.request_id),
            )

    def _validate_object_matches_kind(
        self, request: AssertStatementRequest, value_kind: ValueKind
    ) -> None:
        mapping: dict[ValueKind, object] = {
            ValueKind.ENTITY: request.object_entity_id,
            ValueKind.STRING: request.object_string,
            ValueKind.NUMBER: request.object_number,
            ValueKind.BOOLEAN: request.object_boolean,
            ValueKind.DATETIME: request.object_datetime,
            ValueKind.JSON: request.object_json,
        }
        if mapping[value_kind] is None:
            raise InvalidLiteralTypeError(
                f"Predicate requires a {value_kind.value} object",
                details={"value_kind": value_kind.value},
                request_id=str(request.request_id),
            )

    def _validate_domain(
        self,
        request: AssertStatementRequest,
        predicate_revision_id: uuid.UUID,
        subject_entity_id: uuid.UUID,
    ) -> None:
        domain_ids = set(self._ontology.list_domain_class_ids(predicate_revision_id))
        if not domain_ids:
            return
        subject_types = [
            ontology_class.id
            for ontology_class, _ns in self._entities.list_types(subject_entity_id)
        ]
        if not subject_types:
            raise DomainViolationError(
                "Subject entity has no ontology types for domain validation",
                details={"subject_entity_id": str(subject_entity_id)},
                request_id=str(request.request_id),
            )
        if any(
            self._ontology.class_satisfies(class_id=class_id, allowed_class_ids=domain_ids)
            for class_id in subject_types
        ):
            return
        raise DomainViolationError(
            "Subject entity type is outside the predicate domain",
            details={
                "subject_entity_id": str(subject_entity_id),
                "predicate_key": request.predicate_key,
                "subject_class_ids": [str(item) for item in subject_types],
                "domain_class_ids": [str(item) for item in domain_ids],
            },
            request_id=str(request.request_id),
        )

    def _validate_range(
        self, request: AssertStatementRequest, predicate_revision_id: uuid.UUID
    ) -> None:
        assert request.object_entity_id is not None
        obj = self._entities.get(request.object_entity_id)
        if obj is None or obj.status != EntityStatus.ACTIVE.value:
            raise UnknownEntityError(
                f"Object entity {request.object_entity_id} was not found or is inactive",
                details={"object_entity_id": str(request.object_entity_id)},
                request_id=str(request.request_id),
            )
        range_ids = set(self._ontology.list_range_class_ids(predicate_revision_id))
        if not range_ids:
            return
        object_types = [
            ontology_class.id
            for ontology_class, _ns in self._entities.list_types(request.object_entity_id)
        ]
        if not object_types:
            raise RangeViolationError(
                "Object entity has no ontology types for range validation",
                details={"object_entity_id": str(request.object_entity_id)},
                request_id=str(request.request_id),
            )
        if any(
            self._ontology.class_satisfies(class_id=class_id, allowed_class_ids=range_ids)
            for class_id in object_types
        ):
            return
        raise RangeViolationError(
            "Object entity type is outside the predicate range",
            details={
                "object_entity_id": str(request.object_entity_id),
                "predicate_key": request.predicate_key,
                "object_class_ids": [str(item) for item in object_types],
                "range_class_ids": [str(item) for item in range_ids],
            },
            request_id=str(request.request_id),
        )

    def _to_response(self, statement: Statement) -> StatementResponse:
        predicate = self._ontology.get_predicate(statement.predicate_id)
        if predicate is None:
            raise UnknownPredicateError(
                f"Predicate {statement.predicate_id} was not found",
                details={"predicate_id": str(statement.predicate_id)},
            )
        namespace_key = self._ontology.get_namespace_key(predicate.namespace_id) or "unknown"
        return StatementResponse(
            id=statement.id,
            subject_entity_id=statement.subject_entity_id,
            predicate_id=statement.predicate_id,
            predicate_key=predicate.key,
            namespace_key=namespace_key,
            object_entity_id=statement.object_entity_id,
            object_string=statement.object_string,
            object_number=statement.object_number,
            object_boolean=statement.object_boolean,
            object_datetime=statement.object_datetime,
            object_json=statement.object_json,
            status=StatementStatus(statement.status),
            asserted_at=statement.asserted_at,
            observed_at=statement.observed_at,
            valid_from=statement.valid_from,
            valid_to=statement.valid_to,
            confidence=statement.confidence,
            actor_id=statement.actor_id,
            normalized_object=statement.normalized_object,
            created_at=statement.created_at,
        )
