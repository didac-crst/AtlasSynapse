"""Idempotent Claim class and epistemic binding predicates (Phase A).

Claim interiors are not world beliefs. Retrieval uses CLAIM_BINDING_PREDICATE_KEYS
to exclude epistemic graph material unless include_claims is set.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from semantic_memory.models import (
    Actor,
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
    SYSTEM_ACTOR_NAME,
    stable_seed_id,
)

CLAIM_CLASS_KEY = "Claim"

# Predicates that bind Documents/Claims into the epistemic graph.
# Excluded from default search_memory / get_relevant_context world facts.
CLAIM_BINDING_PREDICATE_KEYS: frozenset[str] = frozenset(
    {
        "makesClaim",
        "claimText",
        "claimSubject",
        "claimPredicateKey",
        "claimObject",
        "claimObjectString",
        "epistemicKind",
        "claimPolarity",
        "claimStatus",
        "claimDerivation",
        "aboutEntity",
    }
)

# key, description, value_kind, cardinality, domain_keys, range_keys
_CLAIM_PREDICATES: tuple[
    tuple[str, str, ValueKind, Cardinality, tuple[str, ...], tuple[str, ...]],
    ...,
] = (
    (
        "makesClaim",
        "Document presents/records a Claim (provenance of speech-act; not author endorsement).",
        ValueKind.ENTITY,
        Cardinality.MANY,
        ("Document",),
        (CLAIM_CLASS_KEY,),
    ),
    (
        "claimText",
        "Original or normalized semantic expression of the Claim (mandatory on every Claim).",
        ValueKind.STRING,
        Cardinality.ONE,
        (CLAIM_CLASS_KEY,),
        (),
    ),
    (
        "claimSubject",
        "Optional structured subject projection of the Claim proposition (non-assertive).",
        ValueKind.ENTITY,
        Cardinality.ONE,
        (CLAIM_CLASS_KEY,),
        ("Thing",),
    ),
    (
        "claimPredicateKey",
        "Optional descriptive predicate key for the Claim proposition. "
        "Never expands into an asserted domain triple.",
        ValueKind.STRING,
        Cardinality.ONE,
        (CLAIM_CLASS_KEY,),
        (),
    ),
    (
        "claimObject",
        "Optional structured entity object projection of the Claim proposition (non-assertive).",
        ValueKind.ENTITY,
        Cardinality.ONE,
        (CLAIM_CLASS_KEY,),
        ("Thing",),
    ),
    (
        "claimObjectString",
        "Optional literal object projection of the Claim proposition (non-assertive).",
        ValueKind.STRING,
        Cardinality.ONE,
        (CLAIM_CLASS_KEY,),
        (),
    ),
    (
        "epistemicKind",
        "Speech-act kind: hypothesis | recommendation | question | assertion (fallback).",
        ValueKind.STRING,
        Cardinality.ONE,
        (CLAIM_CLASS_KEY,),
        (),
    ),
    (
        "claimPolarity",
        "Proposition polarity: positive | negative (orthogonal to claimStatus).",
        ValueKind.STRING,
        Cardinality.ONE,
        (CLAIM_CLASS_KEY,),
        (),
    ),
    (
        "claimStatus",
        "Claim lifecycle: active | rejected | superseded | open | answered.",
        ValueKind.STRING,
        Cardinality.ONE,
        (CLAIM_CLASS_KEY,),
        (),
    ),
    (
        "claimDerivation",
        "How the Claim was obtained: explicit | normalized | inferred.",
        ValueKind.STRING,
        Cardinality.ONE,
        (CLAIM_CLASS_KEY,),
        (),
    ),
    (
        "aboutEntity",
        "Optional denormalized link for retrieving Claims about an entity.",
        ValueKind.ENTITY,
        Cardinality.MANY,
        (CLAIM_CLASS_KEY,),
        ("Thing",),
    ),
)


def _system_actor(session: Session) -> Actor:
    actor = session.scalar(select(Actor).where(Actor.name == SYSTEM_ACTOR_NAME))
    if actor is None:
        raise RuntimeError("system actor missing; run core seed first")
    return actor


def ensure_claim_ontology(session: Session) -> dict[str, Any]:
    """Ensure Claim ⊑ Thing and all Claim-binding predicates exist (idempotent)."""
    namespace = session.scalar(
        select(OntologyNamespace).where(OntologyNamespace.key == CORE_NAMESPACE_KEY)
    )
    if namespace is None:
        raise RuntimeError("core ontology namespace missing; run core seed first")

    actor = _system_actor(session)
    classes: dict[str, OntologyClass] = {
        row.key: row
        for row in session.scalars(
            select(OntologyClass).where(OntologyClass.namespace_id == namespace.id)
        ).all()
    }

    created_classes = 0
    if CLAIM_CLASS_KEY not in classes:
        ontology_class = OntologyClass(
            id=stable_seed_id("class", CORE_NAMESPACE_KEY, CLAIM_CLASS_KEY),
            namespace_id=namespace.id,
            key=CLAIM_CLASS_KEY,
        )
        session.add(ontology_class)
        session.flush()
        class_revision = OntologyClassRevision(
            id=stable_seed_id("class_revision", CORE_NAMESPACE_KEY, CLAIM_CLASS_KEY, "1"),
            class_id=ontology_class.id,
            revision_number=1,
            label=CLAIM_CLASS_KEY,
            description=(
                "Epistemic Claim occurrence: a source-scoped speech-act record. "
                "Interiors are not world beliefs."
            ),
            metadata_json={"seed_extension": "claim_ontology"},
            created_by_actor_id=actor.id,
        )
        session.add(class_revision)
        session.flush()
        ontology_class.current_revision_id = class_revision.id
        classes[CLAIM_CLASS_KEY] = ontology_class
        created_classes = 1

    created_parents = 0
    claim = classes[CLAIM_CLASS_KEY]
    thing = classes.get("Thing")
    if thing is None:
        raise RuntimeError("Thing class missing; run core seed first")
    parent_exists = session.scalar(
        select(OntologyClassParent.id).where(
            OntologyClassParent.child_class_id == claim.id,
            OntologyClassParent.parent_class_id == thing.id,
        )
    )
    if parent_exists is None:
        session.add(
            OntologyClassParent(
                id=stable_seed_id("class_parent", CORE_NAMESPACE_KEY, CLAIM_CLASS_KEY, "Thing"),
                child_class_id=claim.id,
                parent_class_id=thing.id,
            )
        )
        created_parents = 1

    created_predicates = 0
    for key, description, value_kind, cardinality, domains, ranges in _CLAIM_PREDICATES:
        existing = session.scalar(
            select(OntologyPredicate).where(
                OntologyPredicate.namespace_id == namespace.id,
                OntologyPredicate.key == key,
            )
        )
        if existing is not None:
            continue
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
            description=description,
            value_kind=value_kind.value,
            datatype="xsd:string" if value_kind == ValueKind.STRING else None,
            cardinality=cardinality.value,
            is_symmetric=False,
            is_transitive=False,
            metadata_json={"seed_extension": "claim_ontology"},
            created_by_actor_id=actor.id,
        )
        session.add(predicate_revision)
        session.flush()
        predicate.current_revision_id = predicate_revision.id
        for domain_key in domains:
            domain_class = classes.get(domain_key)
            if domain_class is None:
                raise RuntimeError(f"missing domain class {domain_key} for {key}")
            session.add(
                OntologyPredicateDomain(
                    id=stable_seed_id("predicate_domain", CORE_NAMESPACE_KEY, key, domain_key),
                    predicate_revision_id=predicate_revision.id,
                    class_id=domain_class.id,
                )
            )
        for range_key in ranges:
            range_class = classes.get(range_key)
            if range_class is None:
                raise RuntimeError(f"missing range class {range_key} for {key}")
            session.add(
                OntologyPredicateRange(
                    id=stable_seed_id("predicate_range", CORE_NAMESPACE_KEY, key, range_key),
                    predicate_revision_id=predicate_revision.id,
                    class_id=range_class.id,
                )
            )
        created_predicates += 1

    session.flush()
    return {
        "namespace": CORE_NAMESPACE_KEY,
        "created_classes": created_classes,
        "created_parents": created_parents,
        "created_predicates": created_predicates,
    }
