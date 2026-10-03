"""Entity application service."""

from __future__ import annotations

import uuid

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from semantic_memory.exceptions import (
    DbConstraintError,
    DuplicateEntityError,
    IdempotencyKeyReusedError,
    InternalError,
    UnknownClassError,
    UnknownEntityError,
)
from semantic_memory.models import Entity, EntityStatus
from semantic_memory.models.capabilities import Capability
from semantic_memory.repositories.entities import EntityRepository
from semantic_memory.repositories.idempotency import IdempotencyRepository, hash_request_payload
from semantic_memory.repositories.ontology import OntologyRepository
from semantic_memory.schemas.entities import (
    CreateEntityRequest,
    CreateEntityResponse,
    EntityResponse,
    EntityTypeResponse,
    ExternalReferenceResponse,
    ResolutionOutcome,
)
from semantic_memory.services.actors import ActorService
from semantic_memory.services.identity import IdentityService, ResolutionResult
from semantic_memory.validation.normalization import normalize_text


class EntityService:
    def __init__(self, session: Session) -> None:
        self._session = session
        self._actors = ActorService(session)
        self._entities = EntityRepository(session)
        self._ontology = OntologyRepository(session)
        self._identity = IdentityService(session)
        self._idempotency = IdempotencyRepository(session)

    def get(self, entity_id: uuid.UUID) -> EntityResponse:
        entity = self._entities.get(entity_id)
        if entity is None:
            raise UnknownEntityError(
                f"Entity {entity_id} was not found",
                details={"entity_id": str(entity_id)},
            )
        return self._to_entity_response(entity)

    def create_entity(self, request: CreateEntityRequest) -> CreateEntityResponse:
        actor = self._actors.require_active_actor(request.actor_key)
        self._actors.require_capability(actor, Capability.KNOWLEDGE_WRITE)

        ontology_class = self._ontology.get_class_by_key(
            namespace_key=request.namespace_key,
            class_key=request.class_key,
        )
        if ontology_class is None:
            raise UnknownClassError(
                f"Unknown class '{request.namespace_key}:{request.class_key}'",
                details={
                    "namespace_key": request.namespace_key,
                    "class_key": request.class_key,
                },
                request_id=str(request.request_id),
            )

        payload = request.model_dump(
            mode="json",
            exclude={"request_id", "trace_id", "idempotency_key"},
        )
        request_hash = hash_request_payload(payload)

        try:
            # Nested transaction: failed mutations drop the idempotency reservation
            # without poisoning the key or wiping earlier work in the outer session.
            with self._session.begin_nested():
                return self._create_entity_body(
                    request=request,
                    actor_id=actor.id,
                    class_id=ontology_class.id,
                    request_hash=request_hash,
                )
        except IntegrityError as exc:
            raise DbConstraintError(
                "Entity persistence violated a database constraint",
                details={"constraint": "entity_write"},
                request_id=str(request.request_id),
            ) from exc

    def _create_entity_body(
        self,
        *,
        request: CreateEntityRequest,
        actor_id: uuid.UUID,
        class_id: uuid.UUID,
        request_hash: str,
    ) -> CreateEntityResponse:
        reservation, created = self._idempotency.reserve_or_get(
            actor_id=actor_id,
            idempotency_key=request.idempotency_key,
            request_hash=request_hash,
            operation_name="create_entity",
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
            return CreateEntityResponse.model_validate(reservation.response_payload)

        if not created:
            raise InternalError(
                "Idempotency key is reserved by an in-flight request",
                details={"idempotency_key": request.idempotency_key},
                request_id=str(request.request_id),
            )

        external = request.external_reference
        # Serialize resolve+create for this identity key, then resolve under the lock.
        self._entities.acquire_identity_lock(
            class_id=class_id,
            canonical_name=request.canonical_name,
        )
        resolution = self._identity.resolve(
            canonical_name=request.canonical_name,
            class_id=class_id,
            external_source_system=None if external is None else external.source_system,
            external_id=None if external is None else external.external_id,
        )

        response = self._response_for_resolution(
            request,
            resolution,
            actor_id=actor_id,
            class_id=class_id,
        )
        self._idempotency.store_response(reservation, response.model_dump(mode="json"))
        return response

    def _response_for_resolution(
        self,
        request: CreateEntityRequest,
        resolution: ResolutionResult,
        *,
        actor_id: uuid.UUID,
        class_id: uuid.UUID,
    ) -> CreateEntityResponse:
        if resolution.outcome == ResolutionOutcome.AMBIGUOUS:
            return CreateEntityResponse(
                outcome=ResolutionOutcome.AMBIGUOUS,
                entity=None,
                candidates=resolution.candidates,
                request_id=request.request_id,
                reused=False,
            )

        if resolution.outcome == ResolutionOutcome.REUSE:
            assert resolution.entity is not None
            return CreateEntityResponse(
                outcome=ResolutionOutcome.REUSE,
                entity=self._to_entity_response(resolution.entity),
                candidates=[],
                request_id=request.request_id,
                reused=True,
            )

        entity = self._create_new_entity(
            request=request,
            actor_id=actor_id,
            class_id=class_id,
        )
        return CreateEntityResponse(
            outcome=ResolutionOutcome.CREATE,
            entity=self._to_entity_response(entity),
            candidates=[],
            request_id=request.request_id,
            reused=False,
        )

    def _create_new_entity(
        self,
        *,
        request: CreateEntityRequest,
        actor_id: uuid.UUID,
        class_id: uuid.UUID,
    ) -> Entity:
        for alias in request.aliases:
            matches = self._entities.find_by_alias(alias)
            if matches:
                raise DuplicateEntityError(
                    f"Alias '{alias}' already belongs to another entity",
                    details={
                        "alias": alias,
                        "entity_ids": [str(item.id) for item in matches],
                    },
                    request_id=str(request.request_id),
                )

        entity = self._entities.create(
            canonical_name=request.canonical_name.strip(),
            created_by_actor_id=actor_id,
        )
        self._entities.add_type(
            entity_id=entity.id,
            class_id=class_id,
            asserted_by_actor_id=actor_id,
        )

        self._entities.add_alias(entity_id=entity.id, alias=request.canonical_name)
        seen_normalized = {normalize_text(request.canonical_name)}
        for alias in request.aliases:
            normalized = normalize_text(alias)
            if not normalized or normalized in seen_normalized:
                continue
            self._entities.add_alias(entity_id=entity.id, alias=alias.strip())
            seen_normalized.add(normalized)

        if request.external_reference is not None:
            ref = request.external_reference
            self._entities.add_external_reference(
                entity_id=entity.id,
                source_system=ref.source_system,
                external_id=ref.external_id,
                uri=ref.uri,
                label=ref.label,
            )
        return entity

    def _to_entity_response(self, entity: Entity) -> EntityResponse:
        types = [
            EntityTypeResponse(
                class_id=ontology_class.id,
                class_key=ontology_class.key,
                namespace_key=namespace_key,
            )
            for ontology_class, namespace_key in self._entities.list_types(entity.id)
        ]
        aliases = self._entities.list_aliases(entity.id)
        refs = [
            ExternalReferenceResponse(
                source_system=item.source_system,
                external_id=item.external_id,
                uri=item.uri,
                label=item.label,
            )
            for item in self._entities.list_external_references(entity.id)
        ]
        return EntityResponse(
            id=entity.id,
            canonical_name=entity.canonical_name,
            status=EntityStatus(entity.status),
            types=types,
            aliases=aliases,
            external_references=refs,
            created_at=entity.created_at,
            updated_at=entity.updated_at,
        )
