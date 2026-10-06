"""Statement assertion, supersession, retraction, and timeline services."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from semantic_memory.exceptions import (
    DomainViolationError,
    InvalidLiteralTypeError,
    InvalidStateTransitionError,
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
from semantic_memory.repositories.ontology import OntologyRepository
from semantic_memory.repositories.statements import StatementRepository
from semantic_memory.schemas.statements import (
    AssertionOutcome,
    AssertStatementRequest,
    AssertStatementResponse,
    RetractStatementRequest,
    RetractStatementResponse,
    StatementResponse,
    SupersedeStatementRequest,
    SupersedeStatementResponse,
    TimelineEntry,
    TimelineResponse,
)
from semantic_memory.services.actors import ActorService
from semantic_memory.services.conflicts import ConflictService
from semantic_memory.services.mutations import MutationRunner
from semantic_memory.validation.literals import (
    normalize_confidence,
    normalize_object_identity,
    normalize_optional_to_utc,
    normalize_to_utc,
)


class StatementService:
    def __init__(self, session: Session) -> None:
        self._session = session
        self._actors = ActorService(session)
        self._entities = EntityRepository(session)
        self._ontology = OntologyRepository(session)
        self._statements = StatementRepository(session)
        self._mutations = MutationRunner(session)
        self._conflicts = ConflictService(session)

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
        return self._mutations.run(
            actor=actor,
            operation_name="assert_statement",
            request=request,
            response_model=AssertStatementResponse,
            constraint_name="statement_write",
            execute=lambda: self._assert_new(request=request, actor_id=actor.id),
        )

    def supersede_statement(self, request: SupersedeStatementRequest) -> SupersedeStatementResponse:
        actor = self._actors.require_active_actor(request.actor_key)
        self._actors.require_capability(actor, Capability.KNOWLEDGE_WRITE)
        return self._mutations.run(
            actor=actor,
            operation_name="supersede_statement",
            request=request,
            response_model=SupersedeStatementResponse,
            constraint_name="statement_supersede",
            execute=lambda: self._supersede_body(request=request, actor_id=actor.id),
        )

    def retract_statement(self, request: RetractStatementRequest) -> RetractStatementResponse:
        actor = self._actors.require_active_actor(request.actor_key)
        self._actors.require_capability(actor, Capability.KNOWLEDGE_WRITE)
        return self._mutations.run(
            actor=actor,
            operation_name="retract_statement",
            request=request,
            response_model=RetractStatementResponse,
            constraint_name="statement_retract",
            execute=lambda: self._retract_body(request=request),
        )

    def get_timeline(self, entity_id: uuid.UUID) -> TimelineResponse:
        entity = self._entities.get(entity_id)
        if entity is None:
            raise UnknownEntityError(
                f"Entity {entity_id} was not found",
                details={"entity_id": str(entity_id)},
            )
        identity_ids = self._entities.identity_group_ids(entity_id)
        survivor_id = self._entities.resolve_survivor_id(entity_id)
        entries: list[TimelineEntry] = []
        for statement in self._statements.list_for_entity_timeline(identity_ids):
            response = self._to_response(statement)
            sort_time = response.valid_from or response.asserted_at
            entries.append(TimelineEntry(statement=response, sort_time=sort_time))
        return TimelineResponse(entity_id=survivor_id, entries=entries)

    def _supersede_body(
        self, *, request: SupersedeStatementRequest, actor_id: uuid.UUID
    ) -> SupersedeStatementResponse:
        previous = self._statements.get(request.previous_statement_id)
        if previous is None:
            raise UnknownStatementError(
                f"Statement {request.previous_statement_id} was not found",
                details={"statement_id": str(request.previous_statement_id)},
                request_id=str(request.request_id),
            )
        if previous.status != StatementStatus.ASSERTED.value:
            raise InvalidStateTransitionError(
                "Only asserted statements can be superseded",
                details={
                    "statement_id": str(previous.id),
                    "status": previous.status,
                },
                request_id=str(request.request_id),
            )

        # Clear the asserted slot before creating the replacement so cardinality-one
        # predicates can accept the new statement while history is preserved.
        previous.status = StatementStatus.SUPERSEDED.value
        self._session.flush()

        created = self._assert_new(request=request, actor_id=actor_id)
        if created.outcome != AssertionOutcome.CREATE:
            raise InvalidStateTransitionError(
                "Supersession requires creating a replacement statement",
                details={"previous_statement_id": str(previous.id)},
                request_id=str(request.request_id),
            )
        previous.superseded_by_statement_id = created.statement.id
        self._session.flush()
        return SupersedeStatementResponse(
            previous_statement=self._to_response(previous),
            statement=created.statement,
            request_id=request.request_id,
        )

    def _retract_body(self, *, request: RetractStatementRequest) -> RetractStatementResponse:
        statement = self._statements.get(request.statement_id)
        if statement is None:
            raise UnknownStatementError(
                f"Statement {request.statement_id} was not found",
                details={"statement_id": str(request.statement_id)},
                request_id=str(request.request_id),
            )
        if statement.status == StatementStatus.RETRACTED.value:
            return RetractStatementResponse(
                statement=self._to_response(statement),
                request_id=request.request_id,
            )
        if statement.status != StatementStatus.ASSERTED.value:
            raise InvalidStateTransitionError(
                "Only asserted statements can be retracted",
                details={
                    "statement_id": str(statement.id),
                    "status": statement.status,
                },
                request_id=str(request.request_id),
            )
        self._statements.mark_retracted(statement)
        return RetractStatementResponse(
            statement=self._to_response(statement),
            request_id=request.request_id,
        )

    def _assert_new(
        self, *, request: AssertStatementRequest, actor_id: uuid.UUID
    ) -> AssertStatementResponse:
        request = self._normalize_request_datetimes(request)
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

        if revision.cardinality == Cardinality.ONE.value:
            self._statements.acquire_predicate_lock(
                subject_entity_id=subject.id,
                predicate_id=predicate.id,
            )
        else:
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

        others: list[Statement] = []
        if revision.cardinality == Cardinality.ONE.value:
            others = self._statements.find_asserted_for_predicate(
                subject_entity_id=subject.id,
                predicate_id=predicate.id,
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
        conflict_ids: list[uuid.UUID] = []
        if others:
            recorded = self._conflicts.record_cardinality_conflicts(
                new_statement=statement,
                existing=others,
                predicate_key=request.predicate_key,
            )
            conflict_ids = [item.id for item in recorded]
        return AssertStatementResponse(
            outcome=AssertionOutcome.CREATE,
            statement=self._to_response(statement),
            request_id=request.request_id,
            reused=False,
            conflict_ids=conflict_ids,
        )

    def _normalize_request_datetimes(
        self, request: AssertStatementRequest
    ) -> AssertStatementRequest:
        try:
            return request.model_copy(
                update={
                    "observed_at": normalize_optional_to_utc(request.observed_at),
                    "valid_from": normalize_optional_to_utc(request.valid_from),
                    "valid_to": normalize_optional_to_utc(request.valid_to),
                    "object_datetime": (
                        None
                        if request.object_datetime is None
                        else normalize_to_utc(request.object_datetime)
                    ),
                }
            )
        except ValueError as exc:
            raise ValidationFailedError(
                str(exc),
                details={"field": "datetime"},
                request_id=str(request.request_id),
            ) from exc

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
            superseded_by_statement_id=statement.superseded_by_statement_id,
            retracts_statement_id=statement.retracts_statement_id,
            created_at=statement.created_at,
            metadata=dict(statement.metadata_json or {}),
        )
