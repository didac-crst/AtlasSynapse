"""Ontology read-plane service."""

from __future__ import annotations

import uuid

from sqlalchemy.orm import Session

from semantic_memory.exceptions import (
    UnknownClassError,
    UnknownPredicateError,
    ValidationFailedError,
)
from semantic_memory.models import OntologyClass, OntologyPredicate
from semantic_memory.models.enums import AliasTargetType, Cardinality, ValueKind
from semantic_memory.repositories.ontology import OntologyRepository
from semantic_memory.schemas.ontology import (
    OntologyClassResponse,
    OntologyContextResponse,
    OntologyHitType,
    OntologyPredicateResponse,
    OntologySearchHit,
    OntologySearchResponse,
)


class OntologyService:
    def __init__(self, session: Session) -> None:
        self._session = session
        self._ontology = OntologyRepository(session)

    def get_class(
        self,
        *,
        class_key: str | None = None,
        class_id: uuid.UUID | None = None,
        namespace_key: str = "core",
        alias: str | None = None,
    ) -> OntologyClassResponse:
        ontology_class = self._resolve_class(
            class_key=class_key,
            class_id=class_id,
            namespace_key=namespace_key,
            alias=alias,
        )
        return self._to_class_response(ontology_class)

    def get_predicate(
        self,
        *,
        predicate_key: str | None = None,
        predicate_id: uuid.UUID | None = None,
        namespace_key: str = "core",
        alias: str | None = None,
    ) -> OntologyPredicateResponse:
        predicate = self._resolve_predicate(
            predicate_key=predicate_key,
            predicate_id=predicate_id,
            namespace_key=namespace_key,
            alias=alias,
        )
        return self._to_predicate_response(predicate)

    def search_ontology(
        self,
        *,
        query: str,
        namespace_key: str | None = "core",
        limit: int = 25,
    ) -> OntologySearchResponse:
        cleaned = query.strip()
        if not cleaned:
            raise ValidationFailedError("query must not be blank")
        if limit < 1 or limit > 100:
            raise ValidationFailedError(
                "limit must be between 1 and 100",
                details={"limit": limit},
            )
        hits: list[OntologySearchHit] = []
        for ontology_class in self._ontology.search_classes(
            query=cleaned, namespace_key=namespace_key, limit=limit
        ):
            revision = self._ontology.get_current_class_revision(ontology_class)
            ns = (
                self._ontology.get_namespace_key(ontology_class.namespace_id) or namespace_key or ""
            )
            hits.append(
                OntologySearchHit(
                    hit_type=OntologyHitType.CLASS,
                    namespace_key=ns,
                    key=ontology_class.key,
                    label=None if revision is None else revision.label,
                )
            )
        for predicate in self._ontology.search_predicates(
            query=cleaned, namespace_key=namespace_key, limit=limit
        ):
            predicate_revision = self._ontology.get_current_predicate_revision(predicate)
            ns = self._ontology.get_namespace_key(predicate.namespace_id) or namespace_key or ""
            hits.append(
                OntologySearchHit(
                    hit_type=OntologyHitType.PREDICATE,
                    namespace_key=ns,
                    key=predicate.key,
                    label=None if predicate_revision is None else predicate_revision.label,
                )
            )
        if namespace_key is not None:
            alias_row = self._ontology.find_alias(namespace_key=namespace_key, alias=cleaned)
            if alias_row is not None:
                if alias_row.target_type == AliasTargetType.CLASS.value and alias_row.class_id:
                    target = self._ontology.get_class(alias_row.class_id)
                    if target is not None and not target.is_deprecated:
                        hits.append(
                            OntologySearchHit(
                                hit_type=OntologyHitType.ALIAS,
                                namespace_key=namespace_key,
                                key=target.key,
                                alias=alias_row.alias,
                                target_type=AliasTargetType.CLASS,
                            )
                        )
                if (
                    alias_row.target_type == AliasTargetType.PREDICATE.value
                    and alias_row.predicate_id
                ):
                    target_p = self._ontology.get_predicate(alias_row.predicate_id)
                    if target_p is not None and not target_p.is_deprecated:
                        hits.append(
                            OntologySearchHit(
                                hit_type=OntologyHitType.ALIAS,
                                namespace_key=namespace_key,
                                key=target_p.key,
                                alias=alias_row.alias,
                                target_type=AliasTargetType.PREDICATE,
                            )
                        )
        return OntologySearchResponse(query=cleaned, hits=hits[:limit])

    def get_ontology_context(
        self,
        *,
        class_key: str | None = None,
        class_id: uuid.UUID | None = None,
        namespace_key: str = "core",
        alias: str | None = None,
    ) -> OntologyContextResponse:
        ontology_class = self._resolve_class(
            class_key=class_key,
            class_id=class_id,
            namespace_key=namespace_key,
            alias=alias,
        )
        class_response = self._to_class_response(ontology_class)
        ancestor_ids = self._ontology.list_ancestor_class_ids(ontology_class.id)
        predicates: dict[uuid.UUID, OntologyPredicate] = {}
        for ancestor_id in ancestor_ids:
            for predicate in self._ontology.list_predicates_for_domain_class(ancestor_id):
                predicates[predicate.id] = predicate
        applicable = [
            self._to_predicate_response(predicate)
            for predicate in sorted(predicates.values(), key=lambda item: item.key)
        ]
        return OntologyContextResponse(
            ontology_class=class_response,
            applicable_predicates=applicable,
        )

    def _resolve_class(
        self,
        *,
        class_key: str | None,
        class_id: uuid.UUID | None,
        namespace_key: str,
        alias: str | None,
    ) -> OntologyClass:
        provided = [value for value in (class_key, class_id, alias) if value is not None]
        if len(provided) != 1:
            raise ValidationFailedError(
                "Provide exactly one of class_key, class_id, or alias",
                details={
                    "class_key": class_key,
                    "class_id": None if class_id is None else str(class_id),
                    "alias": alias,
                },
            )
        if class_id is not None:
            ontology_class = self._ontology.get_class(class_id)
        elif alias is not None:
            alias_row = self._ontology.find_alias(namespace_key=namespace_key, alias=alias)
            if alias_row is None or alias_row.class_id is None:
                raise UnknownClassError(
                    f"Unknown class alias '{namespace_key}:{alias}'",
                    details={"namespace_key": namespace_key, "alias": alias},
                )
            ontology_class = self._ontology.get_class(alias_row.class_id)
        else:
            assert class_key is not None
            ontology_class = self._ontology.get_class_by_key(
                namespace_key=namespace_key, class_key=class_key
            )
        if ontology_class is None or ontology_class.is_deprecated:
            raise UnknownClassError(
                "Unknown ontology class",
                details={
                    "namespace_key": namespace_key,
                    "class_key": class_key,
                    "class_id": None if class_id is None else str(class_id),
                    "alias": alias,
                },
            )
        return ontology_class

    def _resolve_predicate(
        self,
        *,
        predicate_key: str | None,
        predicate_id: uuid.UUID | None,
        namespace_key: str,
        alias: str | None,
    ) -> OntologyPredicate:
        provided = [value for value in (predicate_key, predicate_id, alias) if value is not None]
        if len(provided) != 1:
            raise ValidationFailedError(
                "Provide exactly one of predicate_key, predicate_id, or alias",
                details={
                    "predicate_key": predicate_key,
                    "predicate_id": None if predicate_id is None else str(predicate_id),
                    "alias": alias,
                },
            )
        if predicate_id is not None:
            predicate = self._ontology.get_predicate(predicate_id)
        elif alias is not None:
            alias_row = self._ontology.find_alias(namespace_key=namespace_key, alias=alias)
            if alias_row is None or alias_row.predicate_id is None:
                raise UnknownPredicateError(
                    f"Unknown predicate alias '{namespace_key}:{alias}'",
                    details={"namespace_key": namespace_key, "alias": alias},
                )
            predicate = self._ontology.get_predicate(alias_row.predicate_id)
        else:
            assert predicate_key is not None
            predicate = self._ontology.get_predicate_by_key(
                namespace_key=namespace_key, predicate_key=predicate_key
            )
        if predicate is None or predicate.is_deprecated:
            raise UnknownPredicateError(
                "Unknown ontology predicate",
                details={
                    "namespace_key": namespace_key,
                    "predicate_key": predicate_key,
                    "predicate_id": None if predicate_id is None else str(predicate_id),
                    "alias": alias,
                },
            )
        return predicate

    def _class_key(self, class_id: uuid.UUID) -> str | None:
        ontology_class = self._ontology.get_class(class_id)
        return None if ontology_class is None else ontology_class.key

    def _to_class_response(self, ontology_class: OntologyClass) -> OntologyClassResponse:
        revision = self._ontology.get_current_class_revision(ontology_class)
        namespace_key = self._ontology.get_namespace_key(ontology_class.namespace_id) or ""
        parent_keys = [
            key
            for parent_id in self._ontology.list_parent_class_ids(ontology_class.id)
            if (key := self._class_key(parent_id)) is not None
        ]
        ancestor_keys = sorted(
            key
            for class_id in self._ontology.list_ancestor_class_ids(ontology_class.id)
            if class_id != ontology_class.id and (key := self._class_key(class_id)) is not None
        )
        descendant_keys = sorted(
            key
            for class_id in self._ontology.list_descendant_class_ids(ontology_class.id)
            if class_id != ontology_class.id and (key := self._class_key(class_id)) is not None
        )
        aliases = [row.alias for row in self._ontology.list_aliases_for_class(ontology_class.id)]
        return OntologyClassResponse(
            id=ontology_class.id,
            namespace_key=namespace_key,
            key=ontology_class.key,
            label=None if revision is None else revision.label,
            description=None if revision is None else revision.description,
            parent_class_keys=parent_keys,
            ancestor_class_keys=ancestor_keys,
            descendant_class_keys=descendant_keys,
            aliases=aliases,
            is_deprecated=ontology_class.is_deprecated,
            metadata={} if revision is None else (revision.metadata_json or {}),
        )

    def _to_predicate_response(self, predicate: OntologyPredicate) -> OntologyPredicateResponse:
        revision = self._ontology.get_current_predicate_revision(predicate)
        namespace_key = self._ontology.get_namespace_key(predicate.namespace_id) or ""
        domain_keys: list[str] = []
        range_keys: list[str] = []
        value_kind = None
        datatype = None
        cardinality = None
        is_symmetric = False
        is_transitive = False
        metadata: dict[str, object] = {}
        label = None
        description = None
        if revision is not None:
            label = revision.label
            description = revision.description
            value_kind = ValueKind(revision.value_kind)
            datatype = revision.datatype
            cardinality = Cardinality(revision.cardinality)
            is_symmetric = revision.is_symmetric
            is_transitive = revision.is_transitive
            metadata = revision.metadata_json or {}
            domain_keys = [
                key
                for class_id in self._ontology.list_domain_class_ids(revision.id)
                if (key := self._class_key(class_id)) is not None
            ]
            range_keys = [
                key
                for class_id in self._ontology.list_range_class_ids(revision.id)
                if (key := self._class_key(class_id)) is not None
            ]
        aliases = [row.alias for row in self._ontology.list_aliases_for_predicate(predicate.id)]
        return OntologyPredicateResponse(
            id=predicate.id,
            namespace_key=namespace_key,
            key=predicate.key,
            label=label,
            description=description,
            value_kind=value_kind,
            datatype=datatype,
            cardinality=cardinality,
            is_symmetric=is_symmetric,
            is_transitive=is_transitive,
            domain_class_keys=sorted(domain_keys),
            range_class_keys=sorted(range_keys),
            aliases=aliases,
            is_deprecated=predicate.is_deprecated,
            metadata=metadata,
        )
