"""Deterministic core ontology seed defined by docs/ontology-model.md."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from semantic_memory.models import (
    Actor,
    ActorStatus,
    ActorType,
    Cardinality,
    OntologyClass,
    OntologyClassParent,
    OntologyClassRevision,
    OntologyNamespace,
    OntologyPredicate,
    OntologyPredicateDomain,
    OntologyPredicateRange,
    OntologyPredicateRevision,
    ValueKind,
)

CORE_NAMESPACE_KEY = "core"
SYSTEM_ACTOR_NAME = "system"

CORE_CLASSES: tuple[str, ...] = (
    "Thing",
    "Agent",
    "Person",
    "Organization",
    "Place",
    "Event",
    "Activity",
    "Project",
    "Document",
    "Observation",
    "Decision",
    "RelationshipContext",
)

CORE_INHERITANCE: tuple[tuple[str, str], ...] = (
    ("Person", "Agent"),
    ("Organization", "Agent"),
    ("Event", "Thing"),
    ("Activity", "Thing"),
    ("Project", "Activity"),
    ("Document", "Thing"),
    ("Observation", "Event"),
    ("Decision", "Event"),
    ("RelationshipContext", "Thing"),
)

# predicate_key, value_kind, cardinality, domain_keys, range_keys
CORE_PREDICATES: tuple[
    tuple[str, ValueKind, Cardinality, tuple[str, ...], tuple[str, ...]], ...
] = (
    ("name", ValueKind.STRING, Cardinality.ONE, ("Thing",), ()),
    ("description", ValueKind.STRING, Cardinality.ONE, ("Thing",), ()),
    ("actor", ValueKind.ENTITY, Cardinality.MANY, ("Event", "Activity", "Decision"), ("Agent",)),
    (
        "hasParticipant",
        ValueKind.ENTITY,
        Cardinality.MANY,
        ("Event", "Activity", "Project", "RelationshipContext"),
        ("Agent",),
    ),
    ("occurredAt", ValueKind.DATETIME, Cardinality.ONE, ("Event", "Observation", "Decision"), ()),
    ("startedAt", ValueKind.DATETIME, Cardinality.ONE, ("Event", "Activity", "Project"), ()),
    ("endedAt", ValueKind.DATETIME, Cardinality.ONE, ("Event", "Activity", "Project"), ()),
    (
        "locatedAt",
        ValueKind.ENTITY,
        Cardinality.MANY,
        ("Event", "Activity", "Place", "Organization"),
        ("Place",),
    ),
    ("relatedTo", ValueKind.ENTITY, Cardinality.MANY, ("Thing",), ("Thing",)),
    ("source", ValueKind.ENTITY, Cardinality.MANY, ("Thing",), ("Document",)),
)


def _get_or_create_system_actor(session: Session) -> Actor:
    existing = session.scalar(select(Actor).where(Actor.name == SYSTEM_ACTOR_NAME))
    if existing is not None:
        return existing
    actor = Actor(
        id=uuid.uuid4(),
        name=SYSTEM_ACTOR_NAME,
        actor_type=ActorType.SYSTEM.value,
        status=ActorStatus.ACTIVE.value,
        capabilities=["admin"],
    )
    session.add(actor)
    session.flush()
    return actor


def seed_core_ontology(session: Session) -> dict[str, Any]:
    """Seed the core namespace, classes, inheritance, and predicates.

    The operation is idempotent: repeated calls leave existing core rows unchanged.
    """
    existing_ns = session.scalar(
        select(OntologyNamespace).where(OntologyNamespace.key == CORE_NAMESPACE_KEY)
    )
    if existing_ns is not None:
        class_count = len(
            session.scalars(
                select(OntologyClass).where(OntologyClass.namespace_id == existing_ns.id)
            ).all()
        )
        predicate_count = len(
            session.scalars(
                select(OntologyPredicate).where(OntologyPredicate.namespace_id == existing_ns.id)
            ).all()
        )
        return {
            "namespace": CORE_NAMESPACE_KEY,
            "created": False,
            "class_count": class_count,
            "predicate_count": predicate_count,
        }

    actor = _get_or_create_system_actor(session)
    namespace = OntologyNamespace(
        id=uuid.uuid4(),
        key=CORE_NAMESPACE_KEY,
        label="Core",
        description="Bootstrap ontology namespace for AtlasSynapse.",
    )
    session.add(namespace)
    session.flush()

    classes: dict[str, OntologyClass] = {}
    for key in CORE_CLASSES:
        ontology_class = OntologyClass(
            id=uuid.uuid4(),
            namespace_id=namespace.id,
            key=key,
        )
        session.add(ontology_class)
        session.flush()
        class_revision = OntologyClassRevision(
            id=uuid.uuid4(),
            class_id=ontology_class.id,
            revision_number=1,
            label=key,
            description=f"Core ontology class {key}.",
            metadata_json={},
            created_by_actor_id=actor.id,
        )
        session.add(class_revision)
        session.flush()
        ontology_class.current_revision_id = class_revision.id
        classes[key] = ontology_class

    for child_key, parent_key in CORE_INHERITANCE:
        session.add(
            OntologyClassParent(
                id=uuid.uuid4(),
                child_class_id=classes[child_key].id,
                parent_class_id=classes[parent_key].id,
            )
        )

    for key, value_kind, cardinality, domain_keys, range_keys in CORE_PREDICATES:
        predicate = OntologyPredicate(
            id=uuid.uuid4(),
            namespace_id=namespace.id,
            key=key,
        )
        session.add(predicate)
        session.flush()
        predicate_revision = OntologyPredicateRevision(
            id=uuid.uuid4(),
            predicate_id=predicate.id,
            revision_number=1,
            label=key,
            description=f"Core ontology predicate {key}.",
            value_kind=value_kind.value,
            datatype="xsd:string" if value_kind == ValueKind.STRING else None,
            cardinality=cardinality.value,
            is_symmetric=key == "relatedTo",
            is_transitive=False,
            metadata_json={},
            created_by_actor_id=actor.id,
        )
        session.add(predicate_revision)
        session.flush()
        predicate.current_revision_id = predicate_revision.id
        for domain_key in domain_keys:
            session.add(
                OntologyPredicateDomain(
                    id=uuid.uuid4(),
                    predicate_revision_id=predicate_revision.id,
                    class_id=classes[domain_key].id,
                )
            )
        for range_key in range_keys:
            session.add(
                OntologyPredicateRange(
                    id=uuid.uuid4(),
                    predicate_revision_id=predicate_revision.id,
                    class_id=classes[range_key].id,
                )
            )

    session.flush()
    return {
        "namespace": CORE_NAMESPACE_KEY,
        "created": True,
        "class_count": len(CORE_CLASSES),
        "predicate_count": len(CORE_PREDICATES),
    }
