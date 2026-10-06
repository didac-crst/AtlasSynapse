"""Idempotent identity-graph predicates (employedBy, spouseOf, parentOf)."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from semantic_memory.models import (
    Cardinality,
    OntologyClass,
    OntologyNamespace,
    OntologyPredicate,
    OntologyPredicateDomain,
    OntologyPredicateRange,
    OntologyPredicateRevision,
    ValueKind,
)
from semantic_memory.seeding.ontology import CORE_NAMESPACE_KEY, SEED_NAMESPACE

_IDENTITY_PREDICATES: tuple[
    tuple[str, str, ValueKind, Cardinality, tuple[str, ...], tuple[str, ...], bool],
    ...,
] = (
    (
        "employedBy",
        "Links a person to an organization they work for (non-decisive; may change over time).",
        ValueKind.ENTITY,
        Cardinality.MANY,
        ("Person",),
        ("Organization",),
        False,
    ),
    (
        "spouseOf",
        "Links a person to their spouse (symmetric; supporting evidence only).",
        ValueKind.ENTITY,
        Cardinality.MANY,
        ("Person",),
        ("Person",),
        True,
    ),
    (
        "parentOf",
        "Links a parent person to a child person (object is the child).",
        ValueKind.ENTITY,
        Cardinality.MANY,
        ("Person",),
        ("Person",),
        False,
    ),
)


def ensure_identity_graph_ontology(session: Session) -> dict[str, Any]:
    namespace = session.scalar(
        select(OntologyNamespace).where(OntologyNamespace.key == CORE_NAMESPACE_KEY)
    )
    if namespace is None:
        raise RuntimeError("core ontology namespace missing; run core seed first")

    created = 0
    for key, description, value_kind, cardinality, domains, ranges, is_symmetric in _IDENTITY_PREDICATES:
        existing = session.scalar(
            select(OntologyPredicate).where(
                OntologyPredicate.namespace_id == namespace.id,
                OntologyPredicate.key == key,
            )
        )
        if existing is not None:
            continue
        pred_id = uuid.uuid5(SEED_NAMESPACE, f"identity-graph-predicate:{key}")
        rev_id = uuid.uuid5(SEED_NAMESPACE, f"identity-graph-predicate-rev:{key}")
        predicate = OntologyPredicate(
            id=pred_id,
            namespace_id=namespace.id,
            key=key,
            is_deprecated=False,
            current_revision_id=None,
        )
        session.add(predicate)
        session.flush()
        revision = OntologyPredicateRevision(
            id=rev_id,
            predicate_id=pred_id,
            revision_number=1,
            label=key,
            description=description,
            value_kind=value_kind.value,
            cardinality=cardinality.value,
            is_symmetric=is_symmetric,
            is_transitive=False,
            metadata_json={},
        )
        session.add(revision)
        session.flush()
        predicate.current_revision_id = rev_id
        for domain_key in domains:
            domain = session.scalar(
                select(OntologyClass).where(
                    OntologyClass.namespace_id == namespace.id,
                    OntologyClass.key == domain_key,
                )
            )
            if domain is None:
                raise RuntimeError(f"missing domain class {domain_key} for {key}")
            session.add(
                OntologyPredicateDomain(
                    id=uuid.uuid5(SEED_NAMESPACE, f"identity-graph-domain:{key}:{domain_key}"),
                    predicate_revision_id=rev_id,
                    class_id=domain.id,
                )
            )
        for range_key in ranges:
            range_cls = session.scalar(
                select(OntologyClass).where(
                    OntologyClass.namespace_id == namespace.id,
                    OntologyClass.key == range_key,
                )
            )
            if range_cls is None:
                raise RuntimeError(f"missing range class {range_key} for {key}")
            session.add(
                OntologyPredicateRange(
                    id=uuid.uuid5(SEED_NAMESPACE, f"identity-graph-range:{key}:{range_key}"),
                    predicate_revision_id=rev_id,
                    class_id=range_cls.id,
                )
            )
        created += 1

    session.flush()
    return {"created_predicates": created}
