"""claim ontology class and epistemic predicates

Revision ID: d4e5f6a7b8c9
Revises: c2d3e4f5a6b7
Create Date: 2026-10-09 09:00:00.000000

Seed Claim ⊑ Thing and Claim-binding predicates for governed knowledge
ingestion Phase A. Idempotent inserts; safe on databases that already ran
ensure_claim_ontology via bootstrap.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d4e5f6a7b8c9"
down_revision: str | Sequence[str] | None = "c2d3e4f5a6b7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_SEED_NS = uuid.UUID("00000000-0000-4000-8000-000000000001")
_CORE_NS = "core"
_CLAIM = "Claim"

_PREDICATES: tuple[
    tuple[str, str, str, str, tuple[str, ...], tuple[str, ...]],
    ...,
] = (
    # key, description, value_kind, cardinality, domains, ranges
    (
        "makesClaim",
        "Document presents/records a Claim (provenance of speech-act; not author endorsement).",
        "entity",
        "many",
        ("Document",),
        (_CLAIM,),
    ),
    (
        "claimText",
        "Original or normalized semantic expression of the Claim (mandatory on every Claim).",
        "string",
        "one",
        (_CLAIM,),
        (),
    ),
    (
        "claimSubject",
        "Optional structured subject projection of the Claim proposition (non-assertive).",
        "entity",
        "one",
        (_CLAIM,),
        ("Thing",),
    ),
    (
        "claimPredicateKey",
        "Optional descriptive predicate key for the Claim proposition. "
        "Never expands into an asserted domain triple.",
        "string",
        "one",
        (_CLAIM,),
        (),
    ),
    (
        "claimObject",
        "Optional structured entity object projection of the Claim proposition (non-assertive).",
        "entity",
        "one",
        (_CLAIM,),
        ("Thing",),
    ),
    (
        "claimObjectString",
        "Optional literal object projection of the Claim proposition (non-assertive).",
        "string",
        "one",
        (_CLAIM,),
        (),
    ),
    (
        "epistemicKind",
        "Speech-act kind: hypothesis | recommendation | question | assertion (fallback).",
        "string",
        "one",
        (_CLAIM,),
        (),
    ),
    (
        "claimPolarity",
        "Proposition polarity: positive | negative (orthogonal to claimStatus).",
        "string",
        "one",
        (_CLAIM,),
        (),
    ),
    (
        "claimStatus",
        "Claim lifecycle: active | rejected | superseded | open | answered.",
        "string",
        "one",
        (_CLAIM,),
        (),
    ),
    (
        "claimDerivation",
        "How the Claim was obtained: explicit | normalized | inferred.",
        "string",
        "one",
        (_CLAIM,),
        (),
    ),
    (
        "aboutEntity",
        "Optional denormalized link for retrieving Claims about an entity.",
        "entity",
        "many",
        (_CLAIM,),
        ("Thing",),
    ),
)


def _sid(*parts: str) -> uuid.UUID:
    return uuid.uuid5(_SEED_NS, ":".join(parts))


def _ensure_class(conn: sa.Connection, *, key: str, description: str) -> uuid.UUID | None:
    ns_id = conn.execute(
        sa.text("SELECT id FROM ontology_namespace WHERE key = :ns"),
        {"ns": _CORE_NS},
    ).scalar()
    if ns_id is None:
        return None
    actor_id = conn.execute(
        sa.text("SELECT id FROM actor WHERE name = 'system' ORDER BY created_at LIMIT 1")
    ).scalar()
    if actor_id is None:
        return None

    class_id = conn.execute(
        sa.text("SELECT c.id FROM ontology_class c WHERE c.namespace_id = :ns AND c.key = :key"),
        {"ns": ns_id, "key": key},
    ).scalar()
    if class_id is not None:
        return class_id

    class_id = _sid("class", _CORE_NS, key)
    rev_id = _sid("class_revision", _CORE_NS, key, "1")
    conn.execute(
        sa.text(
            "INSERT INTO ontology_class "
            "(id, namespace_id, key, current_revision_id, is_deprecated, created_at, updated_at) "
            "VALUES (:id, :ns, :key, NULL, false, NOW(), NOW())"
        ),
        {"id": class_id, "ns": ns_id, "key": key},
    )
    conn.execute(
        sa.text(
            "INSERT INTO ontology_class_revision "
            "(id, class_id, revision_number, label, description, metadata, "
            "created_by_actor_id, created_at) "
            "VALUES (:id, :class_id, 1, :label, :description, "
            "CAST(:metadata AS jsonb), :actor_id, NOW())"
        ),
        {
            "id": rev_id,
            "class_id": class_id,
            "label": key,
            "description": description,
            "metadata": '{"seed_extension": "claim_ontology"}',
            "actor_id": actor_id,
        },
    )
    conn.execute(
        sa.text("UPDATE ontology_class SET current_revision_id = :rev WHERE id = :id"),
        {"rev": rev_id, "id": class_id},
    )
    return class_id


def _ensure_parent(conn: sa.Connection, *, child_key: str, parent_key: str) -> None:
    child_id = conn.execute(
        sa.text(
            "SELECT c.id FROM ontology_class c "
            "JOIN ontology_namespace n ON n.id = c.namespace_id "
            "WHERE n.key = :ns AND c.key = :key"
        ),
        {"ns": _CORE_NS, "key": child_key},
    ).scalar()
    parent_id = conn.execute(
        sa.text(
            "SELECT c.id FROM ontology_class c "
            "JOIN ontology_namespace n ON n.id = c.namespace_id "
            "WHERE n.key = :ns AND c.key = :key"
        ),
        {"ns": _CORE_NS, "key": parent_key},
    ).scalar()
    if child_id is None or parent_id is None:
        return
    exists = conn.execute(
        sa.text(
            "SELECT 1 FROM ontology_class_parent "
            "WHERE child_class_id = :child AND parent_class_id = :parent"
        ),
        {"child": child_id, "parent": parent_id},
    ).scalar()
    if exists is not None:
        return
    conn.execute(
        sa.text(
            "INSERT INTO ontology_class_parent "
            "(id, child_class_id, parent_class_id, created_at) "
            "VALUES (:id, :child, :parent, NOW())"
        ),
        {
            "id": _sid("class_parent", _CORE_NS, child_key, parent_key),
            "child": child_id,
            "parent": parent_id,
        },
    )


def _ensure_predicate(
    conn: sa.Connection,
    *,
    key: str,
    description: str,
    value_kind: str,
    cardinality: str,
    domain_keys: tuple[str, ...],
    range_keys: tuple[str, ...],
) -> None:
    ns_id = conn.execute(
        sa.text("SELECT id FROM ontology_namespace WHERE key = :ns"),
        {"ns": _CORE_NS},
    ).scalar()
    if ns_id is None:
        return
    actor_id = conn.execute(
        sa.text("SELECT id FROM actor WHERE name = 'system' ORDER BY created_at LIMIT 1")
    ).scalar()
    if actor_id is None:
        return

    pred_id = conn.execute(
        sa.text(
            "SELECT p.id FROM ontology_predicate p WHERE p.namespace_id = :ns AND p.key = :key"
        ),
        {"ns": ns_id, "key": key},
    ).scalar()
    datatype = "xsd:string" if value_kind == "string" else None
    if pred_id is None:
        pred_id = _sid("predicate", _CORE_NS, key)
        rev_id = _sid("predicate_revision", _CORE_NS, key, "1")
        conn.execute(
            sa.text(
                "INSERT INTO ontology_predicate "
                "(id, namespace_id, key, current_revision_id, is_deprecated, created_at, updated_at) "
                "VALUES (:id, :ns, :key, NULL, false, NOW(), NOW())"
            ),
            {"id": pred_id, "ns": ns_id, "key": key},
        )
        conn.execute(
            sa.text(
                "INSERT INTO ontology_predicate_revision "
                "(id, predicate_id, revision_number, label, description, value_kind, datatype, "
                "cardinality, is_symmetric, is_transitive, metadata, created_by_actor_id, created_at) "
                "VALUES (:id, :predicate_id, 1, :label, :description, :value_kind, :datatype, "
                ":cardinality, false, false, CAST(:metadata AS jsonb), :actor_id, NOW())"
            ),
            {
                "id": rev_id,
                "predicate_id": pred_id,
                "label": key,
                "description": description,
                "value_kind": value_kind,
                "datatype": datatype,
                "cardinality": cardinality,
                "metadata": '{"seed_extension": "claim_ontology"}',
                "actor_id": actor_id,
            },
        )
        conn.execute(
            sa.text("UPDATE ontology_predicate SET current_revision_id = :rev WHERE id = :id"),
            {"rev": rev_id, "id": pred_id},
        )
    else:
        rev_id = conn.execute(
            sa.text("SELECT current_revision_id FROM ontology_predicate WHERE id = :id"),
            {"id": pred_id},
        ).scalar()
        if rev_id is None:
            return

    for class_key in domain_keys:
        class_id = conn.execute(
            sa.text(
                "SELECT c.id FROM ontology_class c "
                "JOIN ontology_namespace n ON n.id = c.namespace_id "
                "WHERE n.key = :ns AND c.key = :key"
            ),
            {"ns": _CORE_NS, "key": class_key},
        ).scalar()
        if class_id is None:
            continue
        exists = conn.execute(
            sa.text(
                "SELECT 1 FROM ontology_predicate_domain "
                "WHERE predicate_revision_id = :rev AND class_id = :class_id"
            ),
            {"rev": rev_id, "class_id": class_id},
        ).scalar()
        if exists is None:
            conn.execute(
                sa.text(
                    "INSERT INTO ontology_predicate_domain "
                    "(id, predicate_revision_id, class_id) "
                    "VALUES (:id, :rev, :class_id)"
                ),
                {
                    "id": _sid("predicate_domain", _CORE_NS, key, class_key),
                    "rev": rev_id,
                    "class_id": class_id,
                },
            )

    for class_key in range_keys:
        class_id = conn.execute(
            sa.text(
                "SELECT c.id FROM ontology_class c "
                "JOIN ontology_namespace n ON n.id = c.namespace_id "
                "WHERE n.key = :ns AND c.key = :key"
            ),
            {"ns": _CORE_NS, "key": class_key},
        ).scalar()
        if class_id is None:
            continue
        exists = conn.execute(
            sa.text(
                "SELECT 1 FROM ontology_predicate_range "
                "WHERE predicate_revision_id = :rev AND class_id = :class_id"
            ),
            {"rev": rev_id, "class_id": class_id},
        ).scalar()
        if exists is None:
            conn.execute(
                sa.text(
                    "INSERT INTO ontology_predicate_range "
                    "(id, predicate_revision_id, class_id) "
                    "VALUES (:id, :rev, :class_id)"
                ),
                {
                    "id": _sid("predicate_range", _CORE_NS, key, class_key),
                    "rev": rev_id,
                    "class_id": class_id,
                },
            )


def upgrade() -> None:
    conn = op.get_bind()
    _ensure_class(
        conn,
        key=_CLAIM,
        description=(
            "Epistemic Claim occurrence: a source-scoped speech-act record. "
            "Interiors are not world beliefs."
        ),
    )
    _ensure_parent(conn, child_key=_CLAIM, parent_key="Thing")
    for key, description, value_kind, cardinality, domains, ranges in _PREDICATES:
        _ensure_predicate(
            conn,
            key=key,
            description=description,
            value_kind=value_kind,
            cardinality=cardinality,
            domain_keys=domains,
            range_keys=ranges,
        )


def downgrade() -> None:
    # Ontology rows are retained; hard-deletes of ontology concepts are out of policy.
    pass
