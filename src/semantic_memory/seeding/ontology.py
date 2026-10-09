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

# Keep in sync with the initial Alembic migration seed helpers.
SEED_NAMESPACE = uuid.UUID("00000000-0000-4000-8000-000000000001")

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
    "Claim",
)

CORE_INHERITANCE: tuple[tuple[str, str], ...] = (
    ("Agent", "Thing"),
    ("Place", "Thing"),
    ("Person", "Agent"),
    ("Organization", "Agent"),
    ("Event", "Thing"),
    ("Activity", "Thing"),
    ("Project", "Activity"),
    ("Document", "Thing"),
    ("Observation", "Event"),
    ("Decision", "Event"),
    ("RelationshipContext", "Thing"),
    ("Claim", "Thing"),
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
    ("authoredBy", ValueKind.ENTITY, Cardinality.MANY, ("Document",), ("Person",)),
    (
        "publicationContext",
        ValueKind.ENTITY,
        Cardinality.MANY,
        ("Document",),
        ("Organization",),
    ),
    # Claim / epistemic graph (Phase A). Also ensured by ensure_claim_ontology().
    ("makesClaim", ValueKind.ENTITY, Cardinality.MANY, ("Document",), ("Claim",)),
    ("claimText", ValueKind.STRING, Cardinality.ONE, ("Claim",), ()),
    ("claimSubject", ValueKind.ENTITY, Cardinality.ONE, ("Claim",), ("Thing",)),
    ("claimPredicateKey", ValueKind.STRING, Cardinality.ONE, ("Claim",), ()),
    ("claimObject", ValueKind.ENTITY, Cardinality.ONE, ("Claim",), ("Thing",)),
    ("claimObjectString", ValueKind.STRING, Cardinality.ONE, ("Claim",), ()),
    ("epistemicKind", ValueKind.STRING, Cardinality.ONE, ("Claim",), ()),
    ("claimPolarity", ValueKind.STRING, Cardinality.ONE, ("Claim",), ()),
    ("claimStatus", ValueKind.STRING, Cardinality.ONE, ("Claim",), ()),
    ("claimDerivation", ValueKind.STRING, Cardinality.ONE, ("Claim",), ()),
    ("aboutEntity", ValueKind.ENTITY, Cardinality.MANY, ("Claim",), ("Thing",)),
)

# Data-only extensions for Milestone B Phase 7 proofs. No new SQL tables.
# Applied by ensure_rich_event_models() on top of the bootstrap seed.
# Agent→Thing and Place→Thing also live in CORE_INHERITANCE; kept here so
# ensure_rich_event_models remains idempotent on databases seeded before that.
RICH_EVENT_INHERITANCE_FIXES: tuple[tuple[str, str], ...] = (
    ("Agent", "Thing"),
    ("Place", "Thing"),
)

RICH_EVENT_CLASSES: tuple[tuple[str, str], ...] = (
    ("Employment", "RelationshipContext"),
    ("Residence", "RelationshipContext"),
    ("MoveEvent", "Event"),
    ("ProjectParticipation", "RelationshipContext"),
    ("ExperimentRun", "Activity"),
)

RICH_EVENT_DOMAIN_EXTENSIONS: tuple[tuple[str, str], ...] = (
    ("startedAt", "RelationshipContext"),
    ("endedAt", "RelationshipContext"),
    ("locatedAt", "RelationshipContext"),
)


def stable_seed_id(*parts: str) -> uuid.UUID:
    """Return a stable UUID for a seed row identity."""
    return uuid.uuid5(SEED_NAMESPACE, ":".join(parts))


def _get_or_create_system_actor(session: Session) -> Actor:
    existing = session.scalar(select(Actor).where(Actor.name == SYSTEM_ACTOR_NAME))
    if existing is not None:
        return existing
    actor = Actor(
        id=stable_seed_id("actor", "system"),
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
        id=stable_seed_id("namespace", CORE_NAMESPACE_KEY),
        key=CORE_NAMESPACE_KEY,
        label="Core",
        description="Bootstrap ontology namespace for AtlasSynapse.",
    )
    session.add(namespace)
    session.flush()

    classes: dict[str, OntologyClass] = {}
    for key in CORE_CLASSES:
        ontology_class = OntologyClass(
            id=stable_seed_id("class", CORE_NAMESPACE_KEY, key),
            namespace_id=namespace.id,
            key=key,
        )
        session.add(ontology_class)
        session.flush()
        class_revision = OntologyClassRevision(
            id=stable_seed_id("class_revision", CORE_NAMESPACE_KEY, key, "1"),
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
                id=stable_seed_id("class_parent", CORE_NAMESPACE_KEY, child_key, parent_key),
                child_class_id=classes[child_key].id,
                parent_class_id=classes[parent_key].id,
            )
        )

    for key, value_kind, cardinality, domain_keys, range_keys in CORE_PREDICATES:
        predicate = OntologyPredicate(
            id=stable_seed_id("predicate", CORE_NAMESPACE_KEY, key),
            namespace_id=namespace.id,
            key=key,
        )
        session.add(predicate)
        session.flush()
        predicate_revision = OntologyPredicateRevision(
            id=stable_seed_id("predicate_revision", CORE_NAMESPACE_KEY, key, "1"),
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
                    id=stable_seed_id("predicate_domain", CORE_NAMESPACE_KEY, key, domain_key),
                    predicate_revision_id=predicate_revision.id,
                    class_id=classes[domain_key].id,
                )
            )
        for range_key in range_keys:
            session.add(
                OntologyPredicateRange(
                    id=stable_seed_id("predicate_range", CORE_NAMESPACE_KEY, key, range_key),
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


def ensure_rich_event_models(session: Session) -> dict[str, Any]:
    """Ensure representative rich-event classes and predicate domains exist.

    Uses only existing ontology tables. Safe to call repeatedly. Does not create
    domain-specific SQL tables or require a schema migration.
    """
    namespace = session.scalar(
        select(OntologyNamespace).where(OntologyNamespace.key == CORE_NAMESPACE_KEY)
    )
    if namespace is None:
        seed_core_ontology(session)
        namespace = session.scalar(
            select(OntologyNamespace).where(OntologyNamespace.key == CORE_NAMESPACE_KEY)
        )
    assert namespace is not None
    actor = _get_or_create_system_actor(session)

    classes: dict[str, OntologyClass] = {}
    for row in session.scalars(
        select(OntologyClass).where(OntologyClass.namespace_id == namespace.id)
    ).all():
        classes[row.key] = row

    created_classes = 0
    for class_key, _parent_key in RICH_EVENT_CLASSES:
        if class_key in classes:
            continue
        ontology_class = OntologyClass(
            id=stable_seed_id("class", CORE_NAMESPACE_KEY, class_key),
            namespace_id=namespace.id,
            key=class_key,
        )
        session.add(ontology_class)
        session.flush()
        revision = OntologyClassRevision(
            id=stable_seed_id("class_revision", CORE_NAMESPACE_KEY, class_key, "1"),
            class_id=ontology_class.id,
            revision_number=1,
            label=class_key,
            description=f"Rich-event ontology class {class_key}.",
            metadata_json={"seed_extension": "rich_events"},
            created_by_actor_id=actor.id,
        )
        session.add(revision)
        session.flush()
        ontology_class.current_revision_id = revision.id
        classes[class_key] = ontology_class
        created_classes += 1

    created_parents = 0
    for child_key, parent_key in (*RICH_EVENT_INHERITANCE_FIXES, *RICH_EVENT_CLASSES):
        child = classes[child_key]
        parent = classes[parent_key]
        exists = session.scalar(
            select(OntologyClassParent.id).where(
                OntologyClassParent.child_class_id == child.id,
                OntologyClassParent.parent_class_id == parent.id,
            )
        )
        if exists is not None:
            continue
        session.add(
            OntologyClassParent(
                id=stable_seed_id("class_parent", CORE_NAMESPACE_KEY, child_key, parent_key),
                child_class_id=child.id,
                parent_class_id=parent.id,
            )
        )
        created_parents += 1

    created_domains = 0
    for predicate_key, domain_key in RICH_EVENT_DOMAIN_EXTENSIONS:
        predicate = session.scalar(
            select(OntologyPredicate).where(
                OntologyPredicate.namespace_id == namespace.id,
                OntologyPredicate.key == predicate_key,
            )
        )
        if predicate is None or predicate.current_revision_id is None:
            continue
        domain_class = classes[domain_key]
        exists = session.scalar(
            select(OntologyPredicateDomain.id).where(
                OntologyPredicateDomain.predicate_revision_id == predicate.current_revision_id,
                OntologyPredicateDomain.class_id == domain_class.id,
            )
        )
        if exists is not None:
            continue
        session.add(
            OntologyPredicateDomain(
                id=stable_seed_id(
                    "predicate_domain", CORE_NAMESPACE_KEY, predicate_key, domain_key
                ),
                predicate_revision_id=predicate.current_revision_id,
                class_id=domain_class.id,
            )
        )
        created_domains += 1

    session.flush()
    return {
        "namespace": CORE_NAMESPACE_KEY,
        "created_classes": created_classes,
        "created_parents": created_parents,
        "created_domains": created_domains,
    }
