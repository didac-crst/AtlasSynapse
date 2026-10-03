"""Read-only ontology lookups needed by the knowledge plane."""

from __future__ import annotations

import uuid
from collections import deque

from sqlalchemy import select
from sqlalchemy.orm import Session

from semantic_memory.models import (
    OntologyClass,
    OntologyClassParent,
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

    def class_satisfies(self, *, class_id: uuid.UUID, allowed_class_ids: set[uuid.UUID]) -> bool:
        if not allowed_class_ids:
            return True
        return bool(self.list_ancestor_class_ids(class_id) & allowed_class_ids)
