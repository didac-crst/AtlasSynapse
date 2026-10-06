"""Entity application service."""

from __future__ import annotations

import uuid

from sqlalchemy.orm import Session

from semantic_memory.exceptions import (
    DuplicateEntityError,
    InvalidStateTransitionError,
    UnknownClassError,
    UnknownEntityError,
    ValidationFailedError,
)
from semantic_memory.models import Entity, EntityStatus
from semantic_memory.models.capabilities import Capability
from semantic_memory.models.enums import AliasIdentityStrength
from semantic_memory.repositories.entities import EntityRepository
from semantic_memory.repositories.ontology import OntologyRepository
from semantic_memory.repositories.statements import StatementRepository
from semantic_memory.schemas.conflicts import MergeEntityRequest, MergeEntityResponse
from semantic_memory.schemas.entities import (
    AddEntityAliasRequest,
    CreateEntityRequest,
    CreateEntityResponse,
    EntityAliasEntry,
    EntityResponse,
    EntityTypeResponse,
    ExternalReferenceResponse,
    ResolutionOutcome,
)
from semantic_memory.services.actors import ActorService
from semantic_memory.services.identity import IdentityService, ResolutionResult
from semantic_memory.services.mutations import MutationRunner
from semantic_memory.validation.normalization import normalize_text


class EntityService:
    def __init__(self, session: Session) -> None:
        self._session = session
        self._actors = ActorService(session)
        self._entities = EntityRepository(session)
        self._ontology = OntologyRepository(session)
        self._statements = StatementRepository(session)
        self._identity = IdentityService(session)
        self._mutations = MutationRunner(session)

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
        return self._mutations.run(
            actor=actor,
            operation_name="create_entity",
            request=request,
            response_model=CreateEntityResponse,
            constraint_name="entity_write",
            execute=lambda: self._create_entity_body(request=request, actor_id=actor.id),
        )

    def merge_entity(self, request: MergeEntityRequest) -> MergeEntityResponse:
        actor = self._actors.require_active_actor(request.actor_key)
        self._actors.require_capability(actor, Capability.KNOWLEDGE_WRITE)
        return self._mutations.run(
            actor=actor,
            operation_name="merge_entity",
            request=request,
            response_model=MergeEntityResponse,
            constraint_name="entity_merge",
            execute=lambda: self._merge_entity_body(request=request),
        )

    def add_entity_alias(self, request: AddEntityAliasRequest) -> EntityResponse:
        actor = self._actors.require_active_actor(request.actor_key)
        self._actors.require_capability(actor, Capability.KNOWLEDGE_WRITE)
        return self._mutations.run(
            actor=actor,
            operation_name="add_entity_alias",
            request=request,
            response_model=EntityResponse,
            constraint_name="entity_write",
            execute=lambda: self._add_entity_alias_body(request=request),
        )

    def _add_entity_alias_body(self, *, request: AddEntityAliasRequest) -> EntityResponse:
        entity = self._entities.get(request.entity_id)
        if entity is None or entity.status != EntityStatus.ACTIVE.value:
            raise UnknownEntityError(
                f"Entity {request.entity_id} was not found or is inactive",
                details={"entity_id": str(request.entity_id)},
                request_id=str(request.request_id),
            )
        alias = request.alias.strip()
        if not normalize_text(alias):
            raise ValidationFailedError(
                "Alias must contain non-whitespace characters",
                details={"alias": request.alias},
                request_id=str(request.request_id),
            )
        if self._entities.alias_exists_on_other_entity(alias=alias, entity_id=entity.id):
            raise DuplicateEntityError(
                f"Alias '{alias}' already belongs to another entity",
                details={"alias": alias, "entity_id": str(entity.id)},
                request_id=str(request.request_id),
            )
        self._entities.ensure_alias(
            entity_id=entity.id,
            alias=alias,
            identity_strength=request.identity_strength.value,
        )
        return self._to_entity_response(entity)

    def _merge_entity_body(self, *, request: MergeEntityRequest) -> MergeEntityResponse:
        if request.source_entity_id == request.target_entity_id:
            raise ValidationFailedError(
                "source_entity_id and target_entity_id must differ",
                details={
                    "source_entity_id": str(request.source_entity_id),
                    "target_entity_id": str(request.target_entity_id),
                },
                request_id=str(request.request_id),
            )
        self._entities.acquire_merge_locks(
            entity_ids=[request.source_entity_id, request.target_entity_id]
        )
        # Reload under locks so concurrent merges cannot race on stale identity-map rows.
        source = self._entities.get(request.source_entity_id, populate_existing=True)
        target = self._entities.get(request.target_entity_id, populate_existing=True)
        if source is None:
            raise UnknownEntityError(
                f"Source entity {request.source_entity_id} was not found",
                details={"source_entity_id": str(request.source_entity_id)},
                request_id=str(request.request_id),
            )
        if target is None or target.status != EntityStatus.ACTIVE.value:
            raise UnknownEntityError(
                f"Target entity {request.target_entity_id} was not found or is inactive",
                details={"target_entity_id": str(request.target_entity_id)},
                request_id=str(request.request_id),
            )
        if source.status != EntityStatus.ACTIVE.value:
            raise InvalidStateTransitionError(
                f"Source entity {request.source_entity_id} is not active",
                details={
                    "source_entity_id": str(request.source_entity_id),
                    "status": source.status,
                },
                request_id=str(request.request_id),
            )
        # Mark merged first so source aliases no longer block transfer, then
        # redirect future identity resolution onto the survivor (ADR-007).
        # Transferred names become authoritative so subsequent resolve can MATCH.
        merged = self._entities.mark_merged(source, target_entity_id=target.id)
        for alias in [source.canonical_name, *self._entities.list_aliases(source.id)]:
            if self._entities.alias_exists_on_other_entity(alias=alias, entity_id=target.id):
                continue
            self._entities.ensure_alias(
                entity_id=target.id,
                alias=alias,
                identity_strength=AliasIdentityStrength.AUTHORITATIVE.value,
            )
        # Re-point statement endpoints so graph exporters that ignore merge
        # metadata do not drop edges (Cytoscape / raw SPO clients).
        self._statements.reassign_entity_references(
            source_entity_id=source.id,
            target_entity_id=target.id,
        )
        return MergeEntityResponse(
            source=self._to_entity_response(merged),
            target=self._to_entity_response(target),
            request_id=request.request_id,
        )

    def _create_entity_body(
        self,
        *,
        request: CreateEntityRequest,
        actor_id: uuid.UUID,
    ) -> CreateEntityResponse:
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

        external = request.external_reference
        # Serialize resolve+create for this identity key, then resolve under the lock.
        self._entities.acquire_identity_lock(
            class_id=ontology_class.id,
            canonical_name=request.canonical_name,
        )
        resolution = self._identity.resolve(
            canonical_name=request.canonical_name,
            class_id=ontology_class.id,
            external_source_system=None if external is None else external.source_system,
            external_id=None if external is None else external.external_id,
        )

        return self._response_for_resolution(
            request,
            resolution,
            actor_id=actor_id,
            class_id=ontology_class.id,
        )

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
                identity=resolution.identity,
            )

        if resolution.outcome == ResolutionOutcome.REUSE:
            assert resolution.entity is not None
            return CreateEntityResponse(
                outcome=ResolutionOutcome.REUSE,
                entity=self._to_entity_response(resolution.entity),
                candidates=[],
                request_id=request.request_id,
                reused=True,
                identity=resolution.identity,
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
            identity=resolution.identity,
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

        self._entities.add_alias(
            entity_id=entity.id,
            alias=request.canonical_name,
            identity_strength=AliasIdentityStrength.SUPPORTING.value,
        )
        seen_normalized = {normalize_text(request.canonical_name)}
        for alias in request.aliases:
            normalized = normalize_text(alias)
            if not normalized or normalized in seen_normalized:
                continue
            self._entities.add_alias(
                entity_id=entity.id,
                alias=alias.strip(),
                identity_strength=AliasIdentityStrength.SUPPORTING.value,
            )
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
        alias_entries = [
            EntityAliasEntry(
                alias=row.alias,
                identity_strength=AliasIdentityStrength(row.identity_strength),
            )
            for row in self._entities.list_alias_entries(entity.id)
        ]
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
            merged_into_entity_id=entity.merged_into_entity_id,
            types=types,
            aliases=aliases,
            alias_entries=alias_entries,
            external_references=refs,
            created_at=entity.created_at,
            updated_at=entity.updated_at,
        )
