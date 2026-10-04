"""Entity, alias, type, and external-reference persistence."""

from __future__ import annotations

import hashlib
import uuid

from sqlalchemy import ColumnElement, func, select, text
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


def _normalized_canonical_sql() -> ColumnElement[str]:
    collapsed = func.regexp_replace(func.btrim(Entity.canonical_name), r"\s+", " ", "g")
    return func.lower(collapsed)


class EntityRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def get(self, entity_id: uuid.UUID, *, populate_existing: bool = False) -> Entity | None:
        return self._session.get(Entity, entity_id, populate_existing=populate_existing)

    def resolve_survivor_id(self, entity_id: uuid.UUID) -> uuid.UUID:
        """Follow merged_into links to the surviving active entity id."""
        current_id = entity_id
        seen: set[uuid.UUID] = set()
        while current_id not in seen:
            seen.add(current_id)
            entity = self.get(current_id)
            if entity is None or entity.merged_into_entity_id is None:
                return current_id
            current_id = entity.merged_into_entity_id
        return current_id

    def identity_group_ids(self, entity_id: uuid.UUID) -> list[uuid.UUID]:
        """Survivor plus every entity merged into it (for read-side graph views)."""
        survivor_id = self.resolve_survivor_id(entity_id)
        merged_ids = list(
            self._session.scalars(
                select(Entity.id).where(Entity.merged_into_entity_id == survivor_id)
            ).all()
        )
        return [survivor_id, *merged_ids]

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

    def find_by_canonical_name(
        self, canonical_name: str, *, class_id: uuid.UUID | None = None
    ) -> list[Entity]:
        normalized = normalize_text(canonical_name)
        stmt = (
            select(Entity)
            .where(
                Entity.status == EntityStatus.ACTIVE.value,
                _normalized_canonical_sql() == normalized,
            )
            .distinct()
        )
        if class_id is not None:
            stmt = stmt.join(EntityType, EntityType.entity_id == Entity.id).where(
                EntityType.class_id == class_id
            )
        return list(self._session.scalars(stmt).all())

    def find_by_alias(self, alias: str, *, class_id: uuid.UUID | None = None) -> list[Entity]:
        normalized = normalize_text(alias)
        stmt = (
            select(Entity)
            .join(EntityAlias, EntityAlias.entity_id == Entity.id)
            .where(
                EntityAlias.normalized_alias == normalized,
                Entity.status == EntityStatus.ACTIVE.value,
            )
            .distinct()
        )
        if class_id is not None:
            stmt = stmt.join(EntityType, EntityType.entity_id == Entity.id).where(
                EntityType.class_id == class_id
            )
        return list(self._session.scalars(stmt).all())

    def find_candidates(
        self, *, name: str, class_id: uuid.UUID | None = None
    ) -> list[tuple[Entity, str]]:
        """Discover candidates via indexed alias and normalized canonical SQL."""
        by_id: dict[uuid.UUID, tuple[Entity, set[str]]] = {}

        for entity in self.find_by_canonical_name(name, class_id=class_id):
            by_id[entity.id] = (entity, {"canonical_name"})

        for entity in self.find_by_alias(name, class_id=class_id):
            if entity.id in by_id:
                by_id[entity.id][1].add("alias")
            else:
                by_id[entity.id] = (entity, {"alias"})

        return [(entity, "+".join(sorted(reasons))) for entity, reasons in by_id.values()]

    def find_near_name_candidates(
        self, name: str, *, class_id: uuid.UUID | None = None
    ) -> list[Entity]:
        """Find active entities whose name/alias is a token-subset near-match.

        Examples: ``Didac`` ↔ ``Didac Cristobal``. Does not match unrelated
        shared nicknames such as ``Didac Costa`` vs ``Didac Garcia``.
        """
        normalized = normalize_text(name)
        tokens = [token for token in normalized.split(" ") if token]
        if not tokens:
            return []

        by_id: dict[uuid.UUID, Entity] = {}

        # Shorter existing names that are proper prefixes of the incoming name.
        for end in range(1, len(tokens)):
            prefix = " ".join(tokens[:end])
            for entity in self.find_by_canonical_name(prefix, class_id=class_id):
                by_id[entity.id] = entity
            for entity in self.find_by_alias(prefix, class_id=class_id):
                by_id[entity.id] = entity

        # Longer existing names/aliases that start with the incoming name.
        like_pattern = f"{normalized} %"
        canonical_stmt = (
            select(Entity)
            .where(
                Entity.status == EntityStatus.ACTIVE.value,
                _normalized_canonical_sql().like(like_pattern),
            )
            .distinct()
        )
        alias_stmt = (
            select(Entity)
            .join(EntityAlias, EntityAlias.entity_id == Entity.id)
            .where(
                Entity.status == EntityStatus.ACTIVE.value,
                EntityAlias.normalized_alias.like(like_pattern),
            )
            .distinct()
        )
        if class_id is not None:
            canonical_stmt = canonical_stmt.join(
                EntityType, EntityType.entity_id == Entity.id
            ).where(EntityType.class_id == class_id)
            alias_stmt = alias_stmt.join(EntityType, EntityType.entity_id == Entity.id).where(
                EntityType.class_id == class_id
            )
        for entity in self._session.scalars(canonical_stmt).all():
            by_id[entity.id] = entity
        for entity in self._session.scalars(alias_stmt).all():
            by_id[entity.id] = entity

        return list(by_id.values())

    def ensure_alias(self, *, entity_id: uuid.UUID, alias: str) -> EntityAlias | None:
        """Add alias if missing; return None when it already exists on this entity."""
        normalized = normalize_text(alias)
        if not normalized:
            return None
        existing = self._session.scalar(
            select(EntityAlias).where(
                EntityAlias.entity_id == entity_id,
                EntityAlias.normalized_alias == normalized,
            )
        )
        if existing is not None:
            return None
        return self.add_alias(entity_id=entity_id, alias=alias.strip())

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
            select(EntityAlias.id)
            .join(Entity, Entity.id == EntityAlias.entity_id)
            .where(
                EntityAlias.normalized_alias == normalized,
                EntityAlias.entity_id != entity_id,
                Entity.status == EntityStatus.ACTIVE.value,
            )
        )
        return other is not None

    def mark_merged(
        self,
        entity: Entity,
        *,
        target_entity_id: uuid.UUID,
    ) -> Entity:
        entity.status = EntityStatus.MERGED.value
        entity.merged_into_entity_id = target_entity_id
        self._session.flush()
        return entity

    def acquire_merge_locks(self, *, entity_ids: list[uuid.UUID]) -> None:
        """Serialize merges involving these entities; lock in sorted ID order."""
        for entity_id in sorted(set(entity_ids), key=lambda item: item.int):
            material = f"merge:{entity_id}".encode()
            digest = hashlib.sha256(material).digest()[:8]
            lock_key = int.from_bytes(digest, byteorder="big", signed=False) % (2**63)
            self._session.execute(
                text("SELECT pg_advisory_xact_lock(:lock_key)"),
                {"lock_key": lock_key},
            )

    def acquire_identity_lock(self, *, class_id: uuid.UUID, canonical_name: str) -> None:
        """Serialize CREATE/resolution for a normalized class+name identity key."""
        material = f"{class_id}:{normalize_text(canonical_name)}".encode()
        digest = hashlib.sha256(material).digest()[:8]
        lock_key = int.from_bytes(digest, byteorder="big", signed=False) % (2**63)
        self._session.execute(
            text("SELECT pg_advisory_xact_lock(:lock_key)"),
            {"lock_key": lock_key},
        )
