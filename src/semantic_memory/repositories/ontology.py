"""Read-only ontology lookups needed by the knowledge plane."""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from semantic_memory.models import OntologyClass, OntologyNamespace


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
