"""Entity, alias, type, and external-reference persistence."""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from semantic_memory.models import (
    Entity,
    EntityAlias,
    EntityStatus,
    EntityType,
    ExternalReference,
    OntologyClass,
    OntologyNamespace,
)
from semantic_memory.validation.normalization import normalize_text


class EntityRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def get(self, entity_id: uuid.UUID) -> Entity | None:
        return self._session.get(Entity, entity_id)

    def create(
        self,
        *,
        canonical_name: str,
        created_by_actor_id: uuid.UUID,
        status: EntityStatus | str = EntityStatus.ACTIVE,
        entity_id: uuid.UUID | None = None,
    ) -> Entity:
        entity = Entity(
            id=entity_id or uuid.uuid4(),
            canonical_name=canonical_name,
            status=str(status),
            created_by_actor_id=created_by_actor_id,
        )
        self._session.add(entity)
        self._session.flush()
        return entity

    def add_type(
        self,
        *,
        entity_id: uuid.UUID,
        class_id: uuid.UUID,
        asserted_by_actor_id: uuid.UUID,
    ) -> EntityType:
        row = EntityType(
            id=uuid.uuid4(),
            entity_id=entity_id,
            class_id=class_id,
            asserted_by_actor_id=asserted_by_actor_id,
        )
        self._session.add(row)
        self._session.flush()
        return row

    def add_alias(
        self,
        *,
        entity_id: uuid.UUID,
        alias: str,
    ) -> EntityAlias:
        row = EntityAlias(
            id=uuid.uuid4(),
            entity_id=entity_id,
            alias=alias,
            normalized_alias=normalize_text(alias),
        )
        self._session.add(row)
        self._session.flush()
        return row

    def add_external_reference(
        self,
        *,
        entity_id: uuid.UUID,
        source_system: str,
        external_id: str,
        uri: str | None = None,
        label: str | None = None,
    ) -> ExternalReference:
        row = ExternalReference(
            id=uuid.uuid4(),
            entity_id=entity_id,
            source_system=source_system,
            external_id=external_id,
            uri=uri,
            label=label,
        )
        self._session.add(row)
        self._session.flush()
        return row

    def find_by_external_reference(self, *, source_system: str, external_id: str) -> Entity | None:
        return self._session.scalar(
            select(Entity)
            .join(ExternalReference, ExternalReference.entity_id == Entity.id)
            .where(
                ExternalReference.source_system == source_system,
                ExternalReference.external_id == external_id,
                Entity.status == EntityStatus.ACTIVE.value,
            )
        )

    def find_by_canonical_name(self, canonical_name: str) -> list[Entity]:
        normalized = normalize_text(canonical_name)
        entities = self._session.scalars(
            select(Entity).where(Entity.status == EntityStatus.ACTIVE.value)
        ).all()
        return [
            entity for entity in entities if normalize_text(entity.canonical_name) == normalized
        ]

    def find_by_alias(self, alias: str) -> list[Entity]:
        normalized = normalize_text(alias)
        return list(
            self._session.scalars(
                select(Entity)
                .join(EntityAlias, EntityAlias.entity_id == Entity.id)
                .where(
                    EntityAlias.normalized_alias == normalized,
                    Entity.status == EntityStatus.ACTIVE.value,
                )
                .distinct()
            ).all()
        )

    def find_candidates(
        self, *, name: str, class_id: uuid.UUID | None = None
    ) -> list[tuple[Entity, str]]:
        """Discover candidate entities that share normalized identity signals.

        Returns (entity, match_reason) pairs. Exact external/canonical/alias
        matches are expected to be handled by the caller first.
        """
        normalized = normalize_text(name)
        entities = self._session.scalars(
            select(Entity).where(Entity.status == EntityStatus.ACTIVE.value)
        ).all()
        results: list[tuple[Entity, str]] = []
        seen: set[uuid.UUID] = set()
        for entity in entities:
            if class_id is not None and not self.has_type(entity.id, class_id):
                continue
            reasons: list[str] = []
            if normalize_text(entity.canonical_name) == normalized:
                reasons.append("canonical_name")
            aliases = self.list_aliases(entity.id)
            if any(normalize_text(alias) == normalized for alias in aliases):
                reasons.append("alias")
            if reasons and entity.id not in seen:
                seen.add(entity.id)
                results.append((entity, "+".join(reasons)))
        return results

    def has_type(self, entity_id: uuid.UUID, class_id: uuid.UUID) -> bool:
        row = self._session.scalar(
            select(EntityType.id).where(
                EntityType.entity_id == entity_id,
                EntityType.class_id == class_id,
            )
        )
        return row is not None

    def list_aliases(self, entity_id: uuid.UUID) -> list[str]:
        return list(
            self._session.scalars(
                select(EntityAlias.alias).where(EntityAlias.entity_id == entity_id)
            ).all()
        )

    def list_types(self, entity_id: uuid.UUID) -> list[tuple[OntologyClass, str]]:
        rows = self._session.execute(
            select(OntologyClass, OntologyNamespace.key)
            .join(EntityType, EntityType.class_id == OntologyClass.id)
            .join(OntologyNamespace, OntologyNamespace.id == OntologyClass.namespace_id)
            .where(EntityType.entity_id == entity_id)
        ).all()
        return [(row[0], row[1]) for row in rows]

    def list_external_references(self, entity_id: uuid.UUID) -> list[ExternalReference]:
        return list(
            self._session.scalars(
                select(ExternalReference).where(ExternalReference.entity_id == entity_id)
            ).all()
        )

    def alias_exists_on_other_entity(self, *, alias: str, entity_id: uuid.UUID) -> bool:
        normalized = normalize_text(alias)
        other = self._session.scalar(
            select(EntityAlias.id).where(
                EntityAlias.normalized_alias == normalized,
                EntityAlias.entity_id != entity_id,
            )
        )
        return other is not None
