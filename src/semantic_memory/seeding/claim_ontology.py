"""Idempotent Claim class and epistemic binding predicates (Phase A).

Single runtime source of truth for the Claim core-namespace extension.
Claim interiors are not world beliefs. Retrieval uses CLAIM_BINDING_PREDICATE_KEYS
to exclude epistemic graph material unless include_claims is set.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from semantic_memory.exceptions import OntologySeedConflictError
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
CLAIM_PREDICATES: tuple[
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


def _verify_existing_predicate(
    session: Session,
    *,
    predicate: OntologyPredicate,
    key: str,
    value_kind: ValueKind,
    cardinality: Cardinality,
    domain_keys: tuple[str, ...],
    range_keys: tuple[str, ...],
) -> None:
    if predicate.current_revision_id is None:
        raise OntologySeedConflictError(
            f"core.{key} exists without a current revision; refuse Claim ontology bootstrap",
            details={"predicate_key": key},
        )
    revision = session.get(OntologyPredicateRevision, predicate.current_revision_id)
    if revision is None:
        raise OntologySeedConflictError(
            f"core.{key} current revision missing; refuse Claim ontology bootstrap",
            details={"predicate_key": key},
        )
    if revision.value_kind != value_kind.value:
        raise OntologySeedConflictError(
            f"core.{key} value_kind={revision.value_kind!r} incompatible with "
            f"required {value_kind.value!r}",
            details={
                "predicate_key": key,
                "found_value_kind": revision.value_kind,
                "required_value_kind": value_kind.value,
            },
        )
    if revision.cardinality != cardinality.value:
        raise OntologySeedConflictError(
            f"core.{key} cardinality={revision.cardinality!r} incompatible with "
            f"required {cardinality.value!r}",
            details={
                "predicate_key": key,
                "found_cardinality": revision.cardinality,
                "required_cardinality": cardinality.value,
            },
        )
    found_domains = set(
        session.execute(
            select(OntologyClass.key)
            .join(
                OntologyPredicateDomain,
                OntologyPredicateDomain.class_id == OntologyClass.id,
            )
            .where(OntologyPredicateDomain.predicate_revision_id == revision.id)
        ).scalars()
    )
    missing_domains = set(domain_keys) - found_domains
    if missing_domains:
        raise OntologySeedConflictError(
            f"core.{key} missing required domain(s) {sorted(missing_domains)}; "
            "refuse to mutate incompatible predicate",
            details={
                "predicate_key": key,
                "found_domains": sorted(found_domains),
                "required_domains": list(domain_keys),
            },
        )
    found_ranges = set(
        session.execute(
            select(OntologyClass.key)
            .join(
                OntologyPredicateRange,
                OntologyPredicateRange.class_id == OntologyClass.id,
            )
            .where(OntologyPredicateRange.predicate_revision_id == revision.id)
        ).scalars()
    )
    missing_ranges = set(range_keys) - found_ranges
    if missing_ranges:
        raise OntologySeedConflictError(
            f"core.{key} missing required range(s) {sorted(missing_ranges)}; "
            "refuse to mutate incompatible predicate",
            details={
                "predicate_key": key,
                "found_ranges": sorted(found_ranges),
                "required_ranges": list(range_keys),
            },
        )


def ensure_claim_ontology(session: Session) -> dict[str, Any]:
    """Ensure Claim ⊑ Thing and Claim-binding predicates exist (idempotent).

    Existing same-key ontology is reused only when compatible (value kind,
    cardinality, required domains/ranges, Claim→Thing). Incompatible
    collisions fail closed.
    """
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
    for key, description, value_kind, cardinality, domains, ranges in CLAIM_PREDICATES:
        existing = session.scalar(
            select(OntologyPredicate).where(
                OntologyPredicate.namespace_id == namespace.id,
                OntologyPredicate.key == key,
            )
        )
        if existing is not None:
            _verify_existing_predicate(
                session,
                predicate=existing,
                key=key,
                value_kind=value_kind,
                cardinality=cardinality,
                domain_keys=domains,
                range_keys=ranges,
            )
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
