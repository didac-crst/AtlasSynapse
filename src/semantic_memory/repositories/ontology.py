"""Read-only ontology lookups needed by the knowledge plane."""

from __future__ import annotations

import uuid
from collections import deque

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from semantic_memory.models import (
    OntologyAlias,
    OntologyClass,
    OntologyClassParent,
    OntologyClassRevision,
    OntologyNamespace,
    OntologyPredicate,
    OntologyPredicateDomain,
    OntologyPredicateRange,
    OntologyPredicateRevision,
)


class OntologyRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def get_class_by_key(self, *, namespace_key: str, class_key: str) -> OntologyClass | None:
        return self._session.scalar(
            select(OntologyClass)
            .join(OntologyNamespace, OntologyNamespace.id == OntologyClass.namespace_id)
            .where(
                OntologyNamespace.key == namespace_key,
                OntologyClass.key == class_key,
                OntologyClass.is_deprecated.is_(False),
            )
        )

    def get_class(self, class_id: uuid.UUID) -> OntologyClass | None:
        return self._session.get(OntologyClass, class_id)

    def get_current_class_revision(
        self, ontology_class: OntologyClass
    ) -> OntologyClassRevision | None:
        if ontology_class.current_revision_id is None:
            return None
        return self._session.get(OntologyClassRevision, ontology_class.current_revision_id)

    def get_namespace_key(self, namespace_id: uuid.UUID) -> str | None:
        namespace = self._session.get(OntologyNamespace, namespace_id)
        return None if namespace is None else namespace.key

    def get_predicate_by_key(
        self, *, namespace_key: str, predicate_key: str
    ) -> OntologyPredicate | None:
        return self._session.scalar(
            select(OntologyPredicate)
            .join(OntologyNamespace, OntologyNamespace.id == OntologyPredicate.namespace_id)
            .where(
                OntologyNamespace.key == namespace_key,
                OntologyPredicate.key == predicate_key,
                OntologyPredicate.is_deprecated.is_(False),
            )
        )

    def get_predicate(self, predicate_id: uuid.UUID) -> OntologyPredicate | None:
        return self._session.get(OntologyPredicate, predicate_id)

    def get_current_predicate_revision(
        self, predicate: OntologyPredicate
    ) -> OntologyPredicateRevision | None:
        if predicate.current_revision_id is None:
            return None
        return self._session.get(OntologyPredicateRevision, predicate.current_revision_id)

    def list_domain_class_ids(self, predicate_revision_id: uuid.UUID) -> list[uuid.UUID]:
        return list(
            self._session.scalars(
                select(OntologyPredicateDomain.class_id).where(
                    OntologyPredicateDomain.predicate_revision_id == predicate_revision_id
                )
            ).all()
        )

    def list_range_class_ids(self, predicate_revision_id: uuid.UUID) -> list[uuid.UUID]:
        return list(
            self._session.scalars(
                select(OntologyPredicateRange.class_id).where(
                    OntologyPredicateRange.predicate_revision_id == predicate_revision_id
                )
            ).all()
        )

    def list_ancestor_class_ids(self, class_id: uuid.UUID) -> set[uuid.UUID]:
        """Return ``class_id`` and all ancestor class ids via parent links."""
        seen: set[uuid.UUID] = {class_id}
        queue: deque[uuid.UUID] = deque([class_id])
        while queue:
            current = queue.popleft()
            parents = self._session.scalars(
                select(OntologyClassParent.parent_class_id).where(
                    OntologyClassParent.child_class_id == current
                )
            ).all()
            for parent_id in parents:
                if parent_id not in seen:
                    seen.add(parent_id)
                    queue.append(parent_id)
        return seen

    def list_descendant_class_ids(self, class_id: uuid.UUID) -> set[uuid.UUID]:
        """Return ``class_id`` and all descendant class ids via parent links."""
        seen: set[uuid.UUID] = {class_id}
        queue: deque[uuid.UUID] = deque([class_id])
        while queue:
            current = queue.popleft()
            children = self._session.scalars(
                select(OntologyClassParent.child_class_id).where(
                    OntologyClassParent.parent_class_id == current
                )
            ).all()
            for child_id in children:
                if child_id not in seen:
                    seen.add(child_id)
                    queue.append(child_id)
        return seen

    def list_parent_class_ids(self, class_id: uuid.UUID) -> list[uuid.UUID]:
        return list(
            self._session.scalars(
                select(OntologyClassParent.parent_class_id).where(
                    OntologyClassParent.child_class_id == class_id
                )
            ).all()
        )

    def list_aliases_for_class(self, class_id: uuid.UUID) -> list[OntologyAlias]:
        return list(
            self._session.scalars(
                select(OntologyAlias).where(OntologyAlias.class_id == class_id)
            ).all()
        )

    def list_aliases_for_predicate(self, predicate_id: uuid.UUID) -> list[OntologyAlias]:
        return list(
            self._session.scalars(
                select(OntologyAlias).where(OntologyAlias.predicate_id == predicate_id)
            ).all()
        )

    def find_alias(self, *, namespace_key: str, alias: str) -> OntologyAlias | None:
        return self._session.scalar(
            select(OntologyAlias)
            .join(OntologyNamespace, OntologyNamespace.id == OntologyAlias.namespace_id)
            .where(
                OntologyNamespace.key == namespace_key,
                OntologyAlias.alias == alias,
            )
        )

    def search_classes(
        self, *, query: str, namespace_key: str | None, limit: int
    ) -> list[OntologyClass]:
        pattern = f"%{query.strip()}%"
        stmt = (
            select(OntologyClass)
            .join(OntologyNamespace, OntologyNamespace.id == OntologyClass.namespace_id)
            .outerjoin(
                OntologyClassRevision,
                OntologyClassRevision.id == OntologyClass.current_revision_id,
            )
            .where(
                OntologyClass.is_deprecated.is_(False),
                or_(
                    OntologyClass.key.ilike(pattern),
                    OntologyClassRevision.label.ilike(pattern),
                ),
            )
            .order_by(OntologyClass.key.asc())
            .limit(limit)
        )
        if namespace_key is not None:
            stmt = stmt.where(OntologyNamespace.key == namespace_key)
        return list(self._session.scalars(stmt).all())

    def search_predicates(
        self, *, query: str, namespace_key: str | None, limit: int
    ) -> list[OntologyPredicate]:
        pattern = f"%{query.strip()}%"
        stmt = (
            select(OntologyPredicate)
            .join(OntologyNamespace, OntologyNamespace.id == OntologyPredicate.namespace_id)
            .outerjoin(
                OntologyPredicateRevision,
                OntologyPredicateRevision.id == OntologyPredicate.current_revision_id,
            )
            .where(
                OntologyPredicate.is_deprecated.is_(False),
                or_(
                    OntologyPredicate.key.ilike(pattern),
                    OntologyPredicateRevision.label.ilike(pattern),
                ),
            )
            .order_by(OntologyPredicate.key.asc())
            .limit(limit)
        )
        if namespace_key is not None:
            stmt = stmt.where(OntologyNamespace.key == namespace_key)
        return list(self._session.scalars(stmt).all())

    def list_predicates_for_domain_class(self, class_id: uuid.UUID) -> list[OntologyPredicate]:
        """Predicates whose current revision lists ``class_id`` (or ancestor) as domain.

        Conservative context uses exact domain membership; inheritance is applied
        by the service when assembling context.
        """
        return list(
            self._session.scalars(
                select(OntologyPredicate)
                .join(
                    OntologyPredicateRevision,
                    OntologyPredicateRevision.id == OntologyPredicate.current_revision_id,
                )
                .join(
                    OntologyPredicateDomain,
                    OntologyPredicateDomain.predicate_revision_id == OntologyPredicateRevision.id,
                )
                .where(
                    OntologyPredicateDomain.class_id == class_id,
                    OntologyPredicate.is_deprecated.is_(False),
                )
                .distinct()
                .order_by(OntologyPredicate.key.asc())
            ).all()
        )

    def class_satisfies(self, *, class_id: uuid.UUID, allowed_class_ids: set[uuid.UUID]) -> bool:
        if not allowed_class_ids:
            return True
        return bool(self.list_ancestor_class_ids(class_id) & allowed_class_ids)
