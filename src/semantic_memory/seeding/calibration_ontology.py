"""Idempotent ontology extensions needed for semantic-review calibration.

Mirrors the agent-profile concepts present on Satellite production so local
shadow eval / regression tests can retrieve Role, dependsOn, etc.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from semantic_memory.models import (
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
from semantic_memory.seeding.ontology import (
    CORE_NAMESPACE_KEY,
    SEED_NAMESPACE,
    ensure_rich_event_models,
    stable_seed_id,
)

CALIBRATION_CLASSES: tuple[tuple[str, str, str], ...] = (
    ("Role", "Thing", "A position or function held by an agent within an organization or context."),
    ("Goal", "Thing", "A desired future state or outcome pursued by an agent."),
    ("Preference", "Thing", "A durable preference or decision tendency associated with an agent."),
    ("Skill", "Thing", "A capability or area of competence associated with an agent."),
    (
        "System",
        "Thing",
        "A technical or socio-technical system composed of interacting components.",
    ),
    (
        "Constraint",
        "Thing",
        "A hard restriction or requirement that must be satisfied (not merely preferred).",
    ),
)

CALIBRATION_PREDICATES: tuple[
    tuple[str, str, ValueKind, Cardinality, tuple[str, ...], tuple[str, ...]], ...
] = (
    (
        "dependsOn",
        "Directional dependency between things (not symmetric).",
        ValueKind.ENTITY,
        Cardinality.MANY,
        ("Thing",),
        ("Thing",),
    ),
    (
        "holdsRole",
        "Links an agent to a role assignment.",
        ValueKind.ENTITY,
        Cardinality.MANY,
        ("Agent",),
        ("Role",),
    ),
    (
        "roleAt",
        "Links a role to the organization or context where it is held.",
        ValueKind.ENTITY,
        Cardinality.MANY,
        ("Role",),
        ("Organization",),
    ),
    (
        "hasGoal",
        "Links an agent to a goal.",
        ValueKind.ENTITY,
        Cardinality.MANY,
        ("Agent",),
        ("Goal",),
    ),
    (
        "hasSkill",
        "Links an agent to a skill.",
        ValueKind.ENTITY,
        Cardinality.MANY,
        ("Agent",),
        ("Skill",),
    ),
    (
        "hasPreference",
        "Links an agent to a preference.",
        ValueKind.ENTITY,
        Cardinality.MANY,
        ("Agent",),
        ("Preference",),
    ),
    (
        "deployedOn",
        "Links a system or component to the host or platform it runs on.",
        ValueKind.ENTITY,
        Cardinality.MANY,
        ("System",),
        ("System",),
    ),
)


def ensure_calibration_ontology(session: Session) -> dict[str, Any]:
    """Ensure calibration classes/predicates exist. Idempotent."""
    rich = ensure_rich_event_models(session)
    namespace = session.scalar(
        select(OntologyNamespace).where(OntologyNamespace.key == CORE_NAMESPACE_KEY)
    )
    if namespace is None:
        raise RuntimeError("core ontology namespace missing; run core seed first")

    created_classes = 0
    for key, parent_key, description in CALIBRATION_CLASSES:
        existing = session.scalar(
            select(OntologyClass).where(
                OntologyClass.namespace_id == namespace.id,
                OntologyClass.key == key,
            )
        )
        if existing is not None:
            continue
        parent = session.scalar(
            select(OntologyClass).where(
                OntologyClass.namespace_id == namespace.id,
                OntologyClass.key == parent_key,
            )
        )
        if parent is None:
            raise RuntimeError(f"missing parent class {parent_key} for {key}")
        class_id = stable_seed_id("calibration-class", key)
        revision_id = stable_seed_id("calibration-class-rev", key)
        ontology_class = OntologyClass(
            id=class_id,
            namespace_id=namespace.id,
            key=key,
            is_deprecated=False,
            current_revision_id=None,
        )
        session.add(ontology_class)
        session.flush()
        revision = OntologyClassRevision(
            id=revision_id,
            class_id=class_id,
            revision_number=1,
            label=key,
            description=description,
            metadata_json={},
        )
        session.add(revision)
        session.flush()
        ontology_class.current_revision_id = revision_id
        session.add(
            OntologyClassParent(
                id=stable_seed_id("calibration-parent", key, parent_key),
                child_class_id=class_id,
                parent_class_id=parent.id,
            )
        )
        created_classes += 1

    created_predicates = 0
    for key, description, value_kind, cardinality, domains, ranges in CALIBRATION_PREDICATES:
        existing = session.scalar(
            select(OntologyPredicate).where(
                OntologyPredicate.namespace_id == namespace.id,
                OntologyPredicate.key == key,
            )
        )
        if existing is not None:
            continue
        pred_id = stable_seed_id("calibration-predicate", key)
        rev_id = stable_seed_id("calibration-predicate-rev", key)
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
            is_symmetric=False,
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
                    id=uuid.uuid5(SEED_NAMESPACE, f"calibration-domain:{key}:{domain_key}"),
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
                    id=uuid.uuid5(SEED_NAMESPACE, f"calibration-range:{key}:{range_key}"),
                    predicate_revision_id=rev_id,
                    class_id=range_cls.id,
                )
            )
        created_predicates += 1

    session.flush()
    return {
        "created_classes": created_classes,
        "created_predicates": created_predicates,
        "rich_event": rich,
    }
