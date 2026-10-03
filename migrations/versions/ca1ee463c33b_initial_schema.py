"""initial schema

Revision ID: ca1ee463c33b
Revises:
Create Date: 2026-10-03 13:16:46.455203
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "ca1ee463c33b"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Stable seed namespace. Keep in sync with semantic_memory.seeding.ontology.
_SEED_NS = uuid.UUID("00000000-0000-4000-8000-000000000001")

_CORE_CLASSES: tuple[str, ...] = (
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

_CORE_INHERITANCE: tuple[tuple[str, str], ...] = (
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

# key, value_kind, cardinality, domain_keys, range_keys
_CORE_PREDICATES: tuple[tuple[str, str, str, tuple[str, ...], tuple[str, ...]], ...] = (
    ("name", "string", "one", ("Thing",), ()),
    ("description", "string", "one", ("Thing",), ()),
    ("actor", "entity", "many", ("Event", "Activity", "Decision"), ("Agent",)),
    (
        "hasParticipant",
        "entity",
        "many",
        ("Event", "Activity", "Project", "RelationshipContext"),
        ("Agent",),
    ),
    ("occurredAt", "datetime", "one", ("Event", "Observation", "Decision"), ()),
    ("startedAt", "datetime", "one", ("Event", "Activity", "Project"), ()),
    ("endedAt", "datetime", "one", ("Event", "Activity", "Project"), ()),
    (
        "locatedAt",
        "entity",
        "many",
        ("Event", "Activity", "Place", "Organization"),
        ("Place",),
    ),
    ("relatedTo", "entity", "many", ("Thing",), ("Thing",)),
    ("source", "entity", "many", ("Thing",), ("Document",)),
)


def _sid(*parts: str) -> uuid.UUID:
    return uuid.uuid5(_SEED_NS, ":".join(parts))


def _sql_uuid(value: uuid.UUID) -> str:
    return f"'{value}'::uuid"


def _sql_str(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _sql_null_or_str(value: str | None) -> str:
    return "NULL" if value is None else _sql_str(value)


def _seed_core_ontology() -> None:
    """Insert deterministic core ontology rows without importing application code.

    Uses literal SQL so the migration remains valid in offline ``--sql`` mode,
    including JSONB values that Alembic cannot render from Python lists/dicts.
    """
    actor_id = _sid("actor", "system")
    namespace_id = _sid("namespace", "core")
    class_ids = {key: _sid("class", "core", key) for key in _CORE_CLASSES}
    class_revision_ids = {key: _sid("class_revision", "core", key, "1") for key in _CORE_CLASSES}
    predicate_ids = {key: _sid("predicate", "core", key) for key, *_ in _CORE_PREDICATES}
    predicate_revision_ids = {
        key: _sid("predicate_revision", "core", key, "1") for key, *_ in _CORE_PREDICATES
    }

    op.execute(
        sa.text(
            "INSERT INTO actor (id, name, actor_type, status, capabilities) VALUES ("
            f"{_sql_uuid(actor_id)}, {_sql_str('system')}, {_sql_str('system')}, "
            f"{_sql_str('active')}, '[\"admin\"]'::jsonb)"
        )
    )
    op.execute(
        sa.text(
            "INSERT INTO ontology_namespace (id, key, label, description) VALUES ("
            f"{_sql_uuid(namespace_id)}, {_sql_str('core')}, {_sql_str('Core')}, "
            f"{_sql_str('Bootstrap ontology namespace for AtlasSynapse.')})"
        )
    )

    for key in _CORE_CLASSES:
        op.execute(
            sa.text(
                "INSERT INTO ontology_class "
                "(id, namespace_id, key, current_revision_id, is_deprecated) VALUES ("
                f"{_sql_uuid(class_ids[key])}, {_sql_uuid(namespace_id)}, {_sql_str(key)}, "
                "NULL, false)"
            )
        )
    for key in _CORE_CLASSES:
        op.execute(
            sa.text(
                "INSERT INTO ontology_class_revision "
                "(id, class_id, revision_number, label, description, metadata, "
                "created_by_actor_id) VALUES ("
                f"{_sql_uuid(class_revision_ids[key])}, {_sql_uuid(class_ids[key])}, 1, "
                f"{_sql_str(key)}, {_sql_str(f'Core ontology class {key}.')}, "
                f"'{{}}'::jsonb, {_sql_uuid(actor_id)})"
            )
        )
        op.execute(
            sa.text(
                "UPDATE ontology_class SET current_revision_id = "
                f"{_sql_uuid(class_revision_ids[key])} WHERE id = {_sql_uuid(class_ids[key])}"
            )
        )

    for child, parent in _CORE_INHERITANCE:
        op.execute(
            sa.text(
                "INSERT INTO ontology_class_parent "
                "(id, child_class_id, parent_class_id) VALUES ("
                f"{_sql_uuid(_sid('class_parent', 'core', child, parent))}, "
                f"{_sql_uuid(class_ids[child])}, {_sql_uuid(class_ids[parent])})"
            )
        )

    for key, value_kind, cardinality, domain_keys, range_keys in _CORE_PREDICATES:
        op.execute(
            sa.text(
                "INSERT INTO ontology_predicate "
                "(id, namespace_id, key, current_revision_id, is_deprecated) VALUES ("
                f"{_sql_uuid(predicate_ids[key])}, {_sql_uuid(namespace_id)}, {_sql_str(key)}, "
                "NULL, false)"
            )
        )
        datatype = "xsd:string" if value_kind == "string" else None
        is_symmetric = "true" if key == "relatedTo" else "false"
        op.execute(
            sa.text(
                "INSERT INTO ontology_predicate_revision "
                "(id, predicate_id, revision_number, label, description, value_kind, datatype, "
                "cardinality, is_symmetric, is_transitive, metadata, created_by_actor_id) VALUES ("
                f"{_sql_uuid(predicate_revision_ids[key])}, {_sql_uuid(predicate_ids[key])}, 1, "
                f"{_sql_str(key)}, {_sql_str(f'Core ontology predicate {key}.')}, "
                f"{_sql_str(value_kind)}, {_sql_null_or_str(datatype)}, {_sql_str(cardinality)}, "
                f"{is_symmetric}, false, '{{}}'::jsonb, {_sql_uuid(actor_id)})"
            )
        )
        op.execute(
            sa.text(
                "UPDATE ontology_predicate SET current_revision_id = "
                f"{_sql_uuid(predicate_revision_ids[key])} "
                f"WHERE id = {_sql_uuid(predicate_ids[key])}"
            )
        )
        for domain_key in domain_keys:
            op.execute(
                sa.text(
                    "INSERT INTO ontology_predicate_domain "
                    "(id, predicate_revision_id, class_id) VALUES ("
                    f"{_sql_uuid(_sid('predicate_domain', 'core', key, domain_key))}, "
                    f"{_sql_uuid(predicate_revision_ids[key])}, "
                    f"{_sql_uuid(class_ids[domain_key])})"
                )
            )
        for range_key in range_keys:
            op.execute(
                sa.text(
                    "INSERT INTO ontology_predicate_range "
                    "(id, predicate_revision_id, class_id) VALUES ("
                    f"{_sql_uuid(_sid('predicate_range', 'core', key, range_key))}, "
                    f"{_sql_uuid(predicate_revision_ids[key])}, "
                    f"{_sql_uuid(class_ids[range_key])})"
                )
            )


def upgrade() -> None:
    # ### commands auto generated by Alembic - please adjust! ###
    op.create_table(
        "actor",
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("actor_type", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("capabilities", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "actor_type IN ('user', 'agent', 'service', 'system', 'admin')",
            name=op.f("ck_actor_actor_type"),
        ),
        sa.CheckConstraint("status IN ('active', 'disabled')", name=op.f("ck_actor_status")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_actor")),
    )
    op.create_index("ix_actor_actor_type", "actor", ["actor_type"], unique=False)
    op.create_index("ix_actor_status", "actor", ["status"], unique=False)
    op.create_table(
        "ontology_namespace",
        sa.Column("key", sa.Text(), nullable=False),
        sa.Column("label", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_ontology_namespace")),
        sa.UniqueConstraint("key", name="uq_ontology_namespace_key"),
    )
    op.create_table(
        "entity",
        sa.Column("canonical_name", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("merged_into_entity_id", sa.UUID(), nullable=True),
        sa.Column("created_by_actor_id", sa.UUID(), nullable=False),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status IN ('active', 'deprecated', 'merged')", name=op.f("ck_entity_status")
        ),
        sa.ForeignKeyConstraint(
            ["created_by_actor_id"], ["actor.id"], name=op.f("fk_entity_created_by_actor_id_actor")
        ),
        sa.ForeignKeyConstraint(
            ["merged_into_entity_id"],
            ["entity.id"],
            name=op.f("fk_entity_merged_into_entity_id_entity"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_entity")),
    )
    op.create_index("ix_entity_canonical_name", "entity", ["canonical_name"], unique=False)
    op.create_index(
        "ix_entity_merged_into_entity_id", "entity", ["merged_into_entity_id"], unique=False
    )
    op.create_index("ix_entity_status", "entity", ["status"], unique=False)
    op.create_table(
        "ingestion_batch",
        sa.Column("actor_id", sa.UUID(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("item_count", sa.Integer(), nullable=False),
        sa.Column("request_id", sa.UUID(), nullable=False),
        sa.Column("metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'running', 'succeeded', 'failed')",
            name=op.f("ck_ingestion_batch_status"),
        ),
        sa.ForeignKeyConstraint(
            ["actor_id"], ["actor.id"], name=op.f("fk_ingestion_batch_actor_id_actor")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_ingestion_batch")),
    )
    op.create_index("ix_ingestion_batch_actor_id", "ingestion_batch", ["actor_id"], unique=False)
    op.create_index("ix_ingestion_batch_status", "ingestion_batch", ["status"], unique=False)
    op.create_table(
        "ontology_class",
        sa.Column("namespace_id", sa.UUID(), nullable=False),
        sa.Column("key", sa.Text(), nullable=False),
        sa.Column("current_revision_id", sa.UUID(), nullable=True),
        sa.Column("is_deprecated", sa.Boolean(), nullable=False),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["current_revision_id"],
            ["ontology_class_revision.id"],
            name="fk_ontology_class_current_revision",
            use_alter=True,
        ),
        sa.ForeignKeyConstraint(
            ["namespace_id"],
            ["ontology_namespace.id"],
            name=op.f("fk_ontology_class_namespace_id_ontology_namespace"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_ontology_class")),
        sa.UniqueConstraint("namespace_id", "key", name="uq_ontology_class_namespace_key"),
    )
    op.create_index("ix_ontology_class_key", "ontology_class", ["key"], unique=False)
    op.create_index(
        "ix_ontology_class_namespace_id", "ontology_class", ["namespace_id"], unique=False
    )
    op.create_table(
        "ontology_constraint",
        sa.Column("namespace_id", sa.UUID(), nullable=False),
        sa.Column("key", sa.Text(), nullable=False),
        sa.Column("constraint_type", sa.String(length=32), nullable=False),
        sa.Column("expression", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "constraint_type IN ('domain', 'range', 'cardinality', 'custom')",
            name=op.f("ck_ontology_constraint_constraint_type"),
        ),
        sa.ForeignKeyConstraint(
            ["namespace_id"],
            ["ontology_namespace.id"],
            name=op.f("fk_ontology_constraint_namespace_id_ontology_namespace"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_ontology_constraint")),
    )
    op.create_index(
        "ix_ontology_constraint_type", "ontology_constraint", ["constraint_type"], unique=False
    )
    op.create_table(
        "ontology_predicate",
        sa.Column("namespace_id", sa.UUID(), nullable=False),
        sa.Column("key", sa.Text(), nullable=False),
        sa.Column("current_revision_id", sa.UUID(), nullable=True),
        sa.Column("is_deprecated", sa.Boolean(), nullable=False),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["current_revision_id"],
            ["ontology_predicate_revision.id"],
            name="fk_ontology_predicate_current_revision",
            use_alter=True,
        ),
        sa.ForeignKeyConstraint(
            ["namespace_id"],
            ["ontology_namespace.id"],
            name=op.f("fk_ontology_predicate_namespace_id_ontology_namespace"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_ontology_predicate")),
        sa.UniqueConstraint("namespace_id", "key", name="uq_ontology_predicate_namespace_key"),
    )
    op.create_index("ix_ontology_predicate_key", "ontology_predicate", ["key"], unique=False)
    op.create_index(
        "ix_ontology_predicate_namespace_id", "ontology_predicate", ["namespace_id"], unique=False
    )
    op.create_table(
        "ontology_proposal",
        sa.Column("proposed_by_actor_id", sa.UUID(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("proposal_type", sa.Text(), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("base_revision_number", sa.Integer(), nullable=True),
        sa.Column("request_id", sa.UUID(), nullable=False),
        sa.Column("decision_reason", sa.Text(), nullable=True),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status IN ('draft', 'submitted', 'in_review', 'accepted', 'rejected', 'cancelled')",
            name=op.f("ck_ontology_proposal_status"),
        ),
        sa.ForeignKeyConstraint(
            ["proposed_by_actor_id"],
            ["actor.id"],
            name=op.f("fk_ontology_proposal_proposed_by_actor_id_actor"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_ontology_proposal")),
    )
    op.create_index(
        "ix_ontology_proposal_proposed_by_actor_id",
        "ontology_proposal",
        ["proposed_by_actor_id"],
        unique=False,
    )
    op.create_index(
        "ix_ontology_proposal_request_id", "ontology_proposal", ["request_id"], unique=False
    )
    op.create_index("ix_ontology_proposal_status", "ontology_proposal", ["status"], unique=False)
    op.create_table(
        "operation_log",
        sa.Column("actor_id", sa.UUID(), nullable=False),
        sa.Column("operation_name", sa.Text(), nullable=False),
        sa.Column("request_id", sa.UUID(), nullable=False),
        sa.Column("trace_id", sa.UUID(), nullable=True),
        sa.Column("idempotency_key", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("request_payload", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("response_payload", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status IN ('started', 'success', 'rejected', 'failed')",
            name=op.f("ck_operation_log_status"),
        ),
        sa.ForeignKeyConstraint(
            ["actor_id"], ["actor.id"], name=op.f("fk_operation_log_actor_id_actor")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_operation_log")),
    )
    op.create_index("ix_operation_log_actor_id", "operation_log", ["actor_id"], unique=False)
    op.create_index("ix_operation_log_error_code", "operation_log", ["error_code"], unique=False)
    op.create_index(
        "ix_operation_log_operation_name", "operation_log", ["operation_name"], unique=False
    )
    op.create_index("ix_operation_log_request_id", "operation_log", ["request_id"], unique=False)
    op.create_index("ix_operation_log_status", "operation_log", ["status"], unique=False)
    op.create_index("ix_operation_log_trace_id", "operation_log", ["trace_id"], unique=False)
    op.create_table(
        "source",
        sa.Column("source_system", sa.Text(), nullable=True),
        sa.Column("external_id", sa.Text(), nullable=True),
        sa.Column("uri", sa.Text(), nullable=True),
        sa.Column("title", sa.Text(), nullable=True),
        sa.Column("content_hash", sa.Text(), nullable=True),
        sa.Column("reliability", sa.Numeric(precision=5, scale=4), nullable=True),
        sa.Column("retrieved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_by_actor_id", sa.UUID(), nullable=False),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "reliability IS NULL OR (reliability >= 0 AND reliability <= 1)",
            name=op.f("ck_source_reliability_bounds"),
        ),
        sa.ForeignKeyConstraint(
            ["created_by_actor_id"], ["actor.id"], name=op.f("fk_source_created_by_actor_id_actor")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_source")),
    )
    op.create_index("ix_source_content_hash", "source", ["content_hash"], unique=False)
    op.create_index("ix_source_external_id", "source", ["external_id"], unique=False)
    op.create_index("ix_source_source_system", "source", ["source_system"], unique=False)
    op.create_table(
        "entity_alias",
        sa.Column("entity_id", sa.UUID(), nullable=False),
        sa.Column("alias", sa.Text(), nullable=False),
        sa.Column("normalized_alias", sa.Text(), nullable=False),
        sa.Column("source_id", sa.UUID(), nullable=True),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["entity_id"], ["entity.id"], name=op.f("fk_entity_alias_entity_id_entity")
        ),
        sa.ForeignKeyConstraint(
            ["source_id"], ["source.id"], name=op.f("fk_entity_alias_source_id_source")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_entity_alias")),
        sa.UniqueConstraint("entity_id", "alias", name="uq_entity_alias_entity_alias"),
    )
    op.create_index("ix_entity_alias_alias", "entity_alias", ["alias"], unique=False)
    op.create_index(
        "ix_entity_alias_normalized_alias", "entity_alias", ["normalized_alias"], unique=False
    )
    op.create_table(
        "entity_type",
        sa.Column("entity_id", sa.UUID(), nullable=False),
        sa.Column("class_id", sa.UUID(), nullable=False),
        sa.Column("asserted_by_actor_id", sa.UUID(), nullable=False),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["asserted_by_actor_id"],
            ["actor.id"],
            name=op.f("fk_entity_type_asserted_by_actor_id_actor"),
        ),
        sa.ForeignKeyConstraint(
            ["class_id"], ["ontology_class.id"], name=op.f("fk_entity_type_class_id_ontology_class")
        ),
        sa.ForeignKeyConstraint(
            ["entity_id"], ["entity.id"], name=op.f("fk_entity_type_entity_id_entity")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_entity_type")),
        sa.UniqueConstraint("entity_id", "class_id", name="uq_entity_type_entity_class"),
    )
    op.create_index("ix_entity_type_class_id", "entity_type", ["class_id"], unique=False)
    op.create_index("ix_entity_type_entity_id", "entity_type", ["entity_id"], unique=False)
    op.create_table(
        "external_reference",
        sa.Column("entity_id", sa.UUID(), nullable=False),
        sa.Column("source_system", sa.Text(), nullable=False),
        sa.Column("external_id", sa.Text(), nullable=False),
        sa.Column("uri", sa.Text(), nullable=True),
        sa.Column("label", sa.String(length=512), nullable=True),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["entity_id"], ["entity.id"], name=op.f("fk_external_reference_entity_id_entity")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_external_reference")),
        sa.UniqueConstraint(
            "source_system", "external_id", name="uq_external_reference_system_external_id"
        ),
    )
    op.create_index(
        "ix_external_reference_entity_id", "external_reference", ["entity_id"], unique=False
    )
    op.create_index(
        "ix_external_reference_source_system", "external_reference", ["source_system"], unique=False
    )
    op.create_table(
        "idempotency_record",
        sa.Column("actor_id", sa.UUID(), nullable=False),
        sa.Column("idempotency_key", sa.Text(), nullable=False),
        sa.Column("request_hash", sa.Text(), nullable=False),
        sa.Column("operation_name", sa.Text(), nullable=False),
        sa.Column("response_payload", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("operation_log_id", sa.UUID(), nullable=True),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["actor_id"], ["actor.id"], name=op.f("fk_idempotency_record_actor_id_actor")
        ),
        sa.ForeignKeyConstraint(
            ["operation_log_id"],
            ["operation_log.id"],
            name=op.f("fk_idempotency_record_operation_log_id_operation_log"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_idempotency_record")),
        sa.UniqueConstraint("actor_id", "idempotency_key", name="uq_idempotency_record_actor_key"),
    )
    op.create_index(
        "ix_idempotency_record_request_hash", "idempotency_record", ["request_hash"], unique=False
    )
    op.create_table(
        "ontology_alias",
        sa.Column("namespace_id", sa.UUID(), nullable=False),
        sa.Column("alias", sa.Text(), nullable=False),
        sa.Column("target_type", sa.String(length=32), nullable=False),
        sa.Column("class_id", sa.UUID(), nullable=True),
        sa.Column("predicate_id", sa.UUID(), nullable=True),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "(target_type = 'class' AND class_id IS NOT NULL AND predicate_id IS NULL) OR (target_type = 'predicate' AND predicate_id IS NOT NULL AND class_id IS NULL)",
            name=op.f("ck_ontology_alias_alias_target_exclusivity"),
        ),
        sa.CheckConstraint(
            "target_type IN ('class', 'predicate')", name=op.f("ck_ontology_alias_target_type")
        ),
        sa.ForeignKeyConstraint(
            ["class_id"],
            ["ontology_class.id"],
            name=op.f("fk_ontology_alias_class_id_ontology_class"),
        ),
        sa.ForeignKeyConstraint(
            ["namespace_id"],
            ["ontology_namespace.id"],
            name=op.f("fk_ontology_alias_namespace_id_ontology_namespace"),
        ),
        sa.ForeignKeyConstraint(
            ["predicate_id"],
            ["ontology_predicate.id"],
            name=op.f("fk_ontology_alias_predicate_id_ontology_predicate"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_ontology_alias")),
        sa.UniqueConstraint("namespace_id", "alias", name="uq_ontology_alias_namespace_alias"),
    )
    op.create_index("ix_ontology_alias_alias", "ontology_alias", ["alias"], unique=False)
    op.create_index("ix_ontology_alias_class_id", "ontology_alias", ["class_id"], unique=False)
    op.create_index(
        "ix_ontology_alias_predicate_id", "ontology_alias", ["predicate_id"], unique=False
    )
    op.create_table(
        "ontology_change",
        sa.Column("proposal_id", sa.UUID(), nullable=False),
        sa.Column("object_type", sa.String(length=32), nullable=False),
        sa.Column("object_id", sa.UUID(), nullable=False),
        sa.Column("previous_revision_id", sa.UUID(), nullable=True),
        sa.Column("new_revision_id", sa.UUID(), nullable=True),
        sa.Column("change_summary", sa.Text(), nullable=False),
        sa.Column("applied_by_actor_id", sa.UUID(), nullable=False),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "object_type IN ('class', 'predicate', 'constraint', 'alias', 'class_parent')",
            name=op.f("ck_ontology_change_object_type"),
        ),
        sa.ForeignKeyConstraint(
            ["applied_by_actor_id"],
            ["actor.id"],
            name=op.f("fk_ontology_change_applied_by_actor_id_actor"),
        ),
        sa.ForeignKeyConstraint(
            ["proposal_id"],
            ["ontology_proposal.id"],
            name=op.f("fk_ontology_change_proposal_id_ontology_proposal"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_ontology_change")),
    )
    op.create_index("ix_ontology_change_object_id", "ontology_change", ["object_id"], unique=False)
    op.create_index(
        "ix_ontology_change_object_type", "ontology_change", ["object_type"], unique=False
    )
    op.create_index(
        "ix_ontology_change_proposal_id", "ontology_change", ["proposal_id"], unique=False
    )
    op.create_table(
        "ontology_class_parent",
        sa.Column("child_class_id", sa.UUID(), nullable=False),
        sa.Column("parent_class_id", sa.UUID(), nullable=False),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "child_class_id <> parent_class_id",
            name=op.f("ck_ontology_class_parent_no_self_parent"),
        ),
        sa.ForeignKeyConstraint(
            ["child_class_id"],
            ["ontology_class.id"],
            name=op.f("fk_ontology_class_parent_child_class_id_ontology_class"),
        ),
        sa.ForeignKeyConstraint(
            ["parent_class_id"],
            ["ontology_class.id"],
            name=op.f("fk_ontology_class_parent_parent_class_id_ontology_class"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_ontology_class_parent")),
        sa.UniqueConstraint(
            "child_class_id", "parent_class_id", name="uq_ontology_class_parent_pair"
        ),
    )
    op.create_index(
        "ix_ontology_class_parent_child", "ontology_class_parent", ["child_class_id"], unique=False
    )
    op.create_index(
        "ix_ontology_class_parent_parent",
        "ontology_class_parent",
        ["parent_class_id"],
        unique=False,
    )
    op.create_table(
        "ontology_class_revision",
        sa.Column("class_id", sa.UUID(), nullable=False),
        sa.Column("revision_number", sa.Integer(), nullable=False),
        sa.Column("label", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_by_actor_id", sa.UUID(), nullable=True),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["class_id"],
            ["ontology_class.id"],
            name=op.f("fk_ontology_class_revision_class_id_ontology_class"),
        ),
        sa.ForeignKeyConstraint(
            ["created_by_actor_id"],
            ["actor.id"],
            name=op.f("fk_ontology_class_revision_created_by_actor_id_actor"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_ontology_class_revision")),
        sa.UniqueConstraint(
            "class_id", "revision_number", name="uq_ontology_class_revision_class_number"
        ),
    )
    op.create_index(
        "ix_ontology_class_revision_class_id", "ontology_class_revision", ["class_id"], unique=False
    )
    op.create_table(
        "ontology_gate_result",
        sa.Column("proposal_id", sa.UUID(), nullable=False),
        sa.Column("gate_name", sa.Text(), nullable=False),
        sa.Column("decision", sa.String(length=32), nullable=False),
        sa.Column("details", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "decision IN ('pass', 'fail', 'manual_review', 'reuse_recommended')",
            name=op.f("ck_ontology_gate_result_decision"),
        ),
        sa.ForeignKeyConstraint(
            ["proposal_id"],
            ["ontology_proposal.id"],
            name=op.f("fk_ontology_gate_result_proposal_id_ontology_proposal"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_ontology_gate_result")),
        sa.UniqueConstraint(
            "proposal_id", "gate_name", name="uq_ontology_gate_result_proposal_gate"
        ),
    )
    op.create_index(
        "ix_ontology_gate_result_decision", "ontology_gate_result", ["decision"], unique=False
    )
    op.create_index(
        "ix_ontology_gate_result_proposal_id", "ontology_gate_result", ["proposal_id"], unique=False
    )
    op.create_table(
        "ontology_predicate_revision",
        sa.Column("predicate_id", sa.UUID(), nullable=False),
        sa.Column("revision_number", sa.Integer(), nullable=False),
        sa.Column("label", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("value_kind", sa.String(length=32), nullable=False),
        sa.Column("datatype", sa.Text(), nullable=True),
        sa.Column("cardinality", sa.String(length=16), nullable=False),
        sa.Column("is_symmetric", sa.Boolean(), nullable=False),
        sa.Column("is_transitive", sa.Boolean(), nullable=False),
        sa.Column("inverse_predicate_id", sa.UUID(), nullable=True),
        sa.Column("metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_by_actor_id", sa.UUID(), nullable=True),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "cardinality IN ('one', 'many')",
            name=op.f("ck_ontology_predicate_revision_cardinality"),
        ),
        sa.CheckConstraint(
            "value_kind IN ('entity', 'string', 'number', 'boolean', 'datetime', 'json')",
            name=op.f("ck_ontology_predicate_revision_value_kind"),
        ),
        sa.ForeignKeyConstraint(
            ["created_by_actor_id"],
            ["actor.id"],
            name=op.f("fk_ontology_predicate_revision_created_by_actor_id_actor"),
        ),
        sa.ForeignKeyConstraint(
            ["inverse_predicate_id"],
            ["ontology_predicate.id"],
            name=op.f("fk_ontology_predicate_revision_inverse_predicate_id_ontology_predicate"),
        ),
        sa.ForeignKeyConstraint(
            ["predicate_id"],
            ["ontology_predicate.id"],
            name=op.f("fk_ontology_predicate_revision_predicate_id_ontology_predicate"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_ontology_predicate_revision")),
        sa.UniqueConstraint(
            "predicate_id",
            "revision_number",
            name="uq_ontology_predicate_revision_predicate_number",
        ),
    )
    op.create_index(
        "ix_ontology_predicate_revision_predicate_id",
        "ontology_predicate_revision",
        ["predicate_id"],
        unique=False,
    )
    op.create_table(
        "statement",
        sa.Column("subject_entity_id", sa.UUID(), nullable=False),
        sa.Column("predicate_id", sa.UUID(), nullable=False),
        sa.Column("object_entity_id", sa.UUID(), nullable=True),
        sa.Column("object_string", sa.Text(), nullable=True),
        sa.Column("object_number", sa.Numeric(), nullable=True),
        sa.Column("object_boolean", sa.Boolean(), nullable=True),
        sa.Column("object_datetime", sa.DateTime(timezone=True), nullable=True),
        sa.Column("object_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("asserted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("valid_from", sa.DateTime(timezone=True), nullable=True),
        sa.Column("valid_to", sa.DateTime(timezone=True), nullable=True),
        sa.Column("confidence", sa.Numeric(precision=5, scale=4), nullable=True),
        sa.Column("actor_id", sa.UUID(), nullable=False),
        sa.Column("superseded_by_statement_id", sa.UUID(), nullable=True),
        sa.Column("retracts_statement_id", sa.UUID(), nullable=True),
        sa.Column("normalized_object", sa.Text(), nullable=True),
        sa.Column(
            "metadata",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status IN ('asserted', 'superseded', 'retracted')", name=op.f("ck_statement_status")
        ),
        sa.CheckConstraint(
            "((CASE WHEN object_entity_id IS NOT NULL THEN 1 ELSE 0 END) + (CASE WHEN object_string IS NOT NULL THEN 1 ELSE 0 END) + (CASE WHEN object_number IS NOT NULL THEN 1 ELSE 0 END) + (CASE WHEN object_boolean IS NOT NULL THEN 1 ELSE 0 END) + (CASE WHEN object_datetime IS NOT NULL THEN 1 ELSE 0 END) + (CASE WHEN object_json IS NOT NULL THEN 1 ELSE 0 END)) = 1",
            name=op.f("ck_statement_object_exclusivity"),
        ),
        sa.CheckConstraint(
            "confidence IS NULL OR (confidence >= 0 AND confidence <= 1)",
            name=op.f("ck_statement_confidence_bounds"),
        ),
        sa.CheckConstraint(
            "valid_to IS NULL OR valid_from IS NULL OR valid_to >= valid_from",
            name=op.f("ck_statement_valid_interval"),
        ),
        sa.ForeignKeyConstraint(
            ["actor_id"], ["actor.id"], name=op.f("fk_statement_actor_id_actor")
        ),
        sa.ForeignKeyConstraint(
            ["object_entity_id"], ["entity.id"], name=op.f("fk_statement_object_entity_id_entity")
        ),
        sa.ForeignKeyConstraint(
            ["predicate_id"],
            ["ontology_predicate.id"],
            name=op.f("fk_statement_predicate_id_ontology_predicate"),
        ),
        sa.ForeignKeyConstraint(
            ["retracts_statement_id"],
            ["statement.id"],
            name=op.f("fk_statement_retracts_statement_id_statement"),
        ),
        sa.ForeignKeyConstraint(
            ["subject_entity_id"], ["entity.id"], name=op.f("fk_statement_subject_entity_id_entity")
        ),
        sa.ForeignKeyConstraint(
            ["superseded_by_statement_id"],
            ["statement.id"],
            name=op.f("fk_statement_superseded_by_statement_id_statement"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_statement")),
    )
    op.create_index("ix_statement_actor_id", "statement", ["actor_id"], unique=False)
    op.create_index("ix_statement_asserted_at", "statement", ["asserted_at"], unique=False)
    op.create_index(
        "ix_statement_object_entity_id", "statement", ["object_entity_id"], unique=False
    )
    op.create_index("ix_statement_predicate_id", "statement", ["predicate_id"], unique=False)
    op.create_index(
        "ix_statement_semantic_identity",
        "statement",
        [
            "subject_entity_id",
            "predicate_id",
            "object_entity_id",
            "object_string",
            "object_number",
            "object_boolean",
            "object_datetime",
            "valid_from",
            "valid_to",
        ],
        unique=False,
    )
    op.create_index("ix_statement_status", "statement", ["status"], unique=False)
    op.create_index(
        "ix_statement_subject_entity_id", "statement", ["subject_entity_id"], unique=False
    )
    op.create_index("ix_statement_valid_from", "statement", ["valid_from"], unique=False)
    op.create_index("ix_statement_valid_to", "statement", ["valid_to"], unique=False)
    op.create_table(
        "conflict",
        sa.Column("statement_a_id", sa.UUID(), nullable=False),
        sa.Column("statement_b_id", sa.UUID(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("conflict_type", sa.Text(), nullable=False),
        sa.Column("details", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("resolved_by_actor_id", sa.UUID(), nullable=True),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status IN ('open', 'resolved', 'dismissed')", name=op.f("ck_conflict_status")
        ),
        sa.ForeignKeyConstraint(
            ["resolved_by_actor_id"],
            ["actor.id"],
            name=op.f("fk_conflict_resolved_by_actor_id_actor"),
        ),
        sa.ForeignKeyConstraint(
            ["statement_a_id"], ["statement.id"], name=op.f("fk_conflict_statement_a_id_statement")
        ),
        sa.ForeignKeyConstraint(
            ["statement_b_id"], ["statement.id"], name=op.f("fk_conflict_statement_b_id_statement")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_conflict")),
    )
    op.create_index("ix_conflict_statement_a_id", "conflict", ["statement_a_id"], unique=False)
    op.create_index("ix_conflict_statement_b_id", "conflict", ["statement_b_id"], unique=False)
    op.create_index("ix_conflict_status", "conflict", ["status"], unique=False)
    op.create_table(
        "ontology_predicate_domain",
        sa.Column("predicate_revision_id", sa.UUID(), nullable=False),
        sa.Column("class_id", sa.UUID(), nullable=False),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["class_id"],
            ["ontology_class.id"],
            name=op.f("fk_ontology_predicate_domain_class_id_ontology_class"),
        ),
        sa.ForeignKeyConstraint(
            ["predicate_revision_id"],
            ["ontology_predicate_revision.id"],
            name=op.f(
                "fk_ontology_predicate_domain_predicate_revision_id_ontology_predicate_revision"
            ),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_ontology_predicate_domain")),
        sa.UniqueConstraint(
            "predicate_revision_id", "class_id", name="uq_ontology_predicate_domain_revision_class"
        ),
    )
    op.create_index(
        "ix_ontology_predicate_domain_class",
        "ontology_predicate_domain",
        ["class_id"],
        unique=False,
    )
    op.create_index(
        "ix_ontology_predicate_domain_revision",
        "ontology_predicate_domain",
        ["predicate_revision_id"],
        unique=False,
    )
    op.create_table(
        "ontology_predicate_range",
        sa.Column("predicate_revision_id", sa.UUID(), nullable=False),
        sa.Column("class_id", sa.UUID(), nullable=False),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["class_id"],
            ["ontology_class.id"],
            name=op.f("fk_ontology_predicate_range_class_id_ontology_class"),
        ),
        sa.ForeignKeyConstraint(
            ["predicate_revision_id"],
            ["ontology_predicate_revision.id"],
            name=op.f(
                "fk_ontology_predicate_range_predicate_revision_id_ontology_predicate_revision"
            ),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_ontology_predicate_range")),
        sa.UniqueConstraint(
            "predicate_revision_id", "class_id", name="uq_ontology_predicate_range_revision_class"
        ),
    )
    op.create_index(
        "ix_ontology_predicate_range_class", "ontology_predicate_range", ["class_id"], unique=False
    )
    op.create_index(
        "ix_ontology_predicate_range_revision",
        "ontology_predicate_range",
        ["predicate_revision_id"],
        unique=False,
    )
    op.create_table(
        "statement_evidence",
        sa.Column("statement_id", sa.UUID(), nullable=False),
        sa.Column("source_id", sa.UUID(), nullable=False),
        sa.Column("excerpt", sa.Text(), nullable=True),
        sa.Column("locator", sa.Text(), nullable=True),
        sa.Column("extraction_confidence", sa.Numeric(precision=5, scale=4), nullable=True),
        sa.Column("asserted_by_actor_id", sa.UUID(), nullable=False),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "extraction_confidence IS NULL OR (extraction_confidence >= 0 AND extraction_confidence <= 1)",
            name=op.f("ck_statement_evidence_extraction_confidence_bounds"),
        ),
        sa.ForeignKeyConstraint(
            ["asserted_by_actor_id"],
            ["actor.id"],
            name=op.f("fk_statement_evidence_asserted_by_actor_id_actor"),
        ),
        sa.ForeignKeyConstraint(
            ["source_id"], ["source.id"], name=op.f("fk_statement_evidence_source_id_source")
        ),
        sa.ForeignKeyConstraint(
            ["statement_id"],
            ["statement.id"],
            name=op.f("fk_statement_evidence_statement_id_statement"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_statement_evidence")),
    )
    op.create_index(
        "ix_statement_evidence_source_id", "statement_evidence", ["source_id"], unique=False
    )
    op.create_index(
        "ix_statement_evidence_statement_id", "statement_evidence", ["statement_id"], unique=False
    )
    op.create_table(
        "statement_qualifier",
        sa.Column("statement_id", sa.UUID(), nullable=False),
        sa.Column("predicate_id", sa.UUID(), nullable=False),
        sa.Column("object_entity_id", sa.UUID(), nullable=True),
        sa.Column("object_string", sa.Text(), nullable=True),
        sa.Column("object_number", sa.Numeric(), nullable=True),
        sa.Column("object_boolean", sa.Boolean(), nullable=True),
        sa.Column("object_datetime", sa.DateTime(timezone=True), nullable=True),
        sa.Column("object_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "((CASE WHEN object_entity_id IS NOT NULL THEN 1 ELSE 0 END) + (CASE WHEN object_string IS NOT NULL THEN 1 ELSE 0 END) + (CASE WHEN object_number IS NOT NULL THEN 1 ELSE 0 END) + (CASE WHEN object_boolean IS NOT NULL THEN 1 ELSE 0 END) + (CASE WHEN object_datetime IS NOT NULL THEN 1 ELSE 0 END) + (CASE WHEN object_json IS NOT NULL THEN 1 ELSE 0 END)) = 1",
            name=op.f("ck_statement_qualifier_object_exclusivity"),
        ),
        sa.ForeignKeyConstraint(
            ["object_entity_id"],
            ["entity.id"],
            name=op.f("fk_statement_qualifier_object_entity_id_entity"),
        ),
        sa.ForeignKeyConstraint(
            ["predicate_id"],
            ["ontology_predicate.id"],
            name=op.f("fk_statement_qualifier_predicate_id_ontology_predicate"),
        ),
        sa.ForeignKeyConstraint(
            ["statement_id"],
            ["statement.id"],
            name=op.f("fk_statement_qualifier_statement_id_statement"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_statement_qualifier")),
    )
    op.create_index(
        "ix_statement_qualifier_predicate_id", "statement_qualifier", ["predicate_id"], unique=False
    )
    op.create_index(
        "ix_statement_qualifier_statement_id", "statement_qualifier", ["statement_id"], unique=False
    )
    # ### end Alembic commands ###

    _seed_core_ontology()


def downgrade() -> None:
    # ### commands auto generated by Alembic - please adjust! ###
    op.drop_index("ix_statement_qualifier_statement_id", table_name="statement_qualifier")
    op.drop_index("ix_statement_qualifier_predicate_id", table_name="statement_qualifier")
    op.drop_table("statement_qualifier")
    op.drop_index("ix_statement_evidence_statement_id", table_name="statement_evidence")
    op.drop_index("ix_statement_evidence_source_id", table_name="statement_evidence")
    op.drop_table("statement_evidence")
    op.drop_index("ix_ontology_predicate_range_revision", table_name="ontology_predicate_range")
    op.drop_index("ix_ontology_predicate_range_class", table_name="ontology_predicate_range")
    op.drop_table("ontology_predicate_range")
    op.drop_index("ix_ontology_predicate_domain_revision", table_name="ontology_predicate_domain")
    op.drop_index("ix_ontology_predicate_domain_class", table_name="ontology_predicate_domain")
    op.drop_table("ontology_predicate_domain")
    op.drop_index("ix_conflict_status", table_name="conflict")
    op.drop_index("ix_conflict_statement_b_id", table_name="conflict")
    op.drop_index("ix_conflict_statement_a_id", table_name="conflict")
    op.drop_table("conflict")
    op.drop_index("ix_statement_valid_to", table_name="statement")
    op.drop_index("ix_statement_valid_from", table_name="statement")
    op.drop_index("ix_statement_subject_entity_id", table_name="statement")
    op.drop_index("ix_statement_status", table_name="statement")
    op.drop_index("ix_statement_semantic_identity", table_name="statement")
    op.drop_index("ix_statement_predicate_id", table_name="statement")
    op.drop_index("ix_statement_object_entity_id", table_name="statement")
    op.drop_index("ix_statement_asserted_at", table_name="statement")
    op.drop_index("ix_statement_actor_id", table_name="statement")
    op.drop_table("statement")
    op.drop_index(
        "ix_ontology_predicate_revision_predicate_id", table_name="ontology_predicate_revision"
    )
    op.drop_table("ontology_predicate_revision")
    op.drop_index("ix_ontology_gate_result_proposal_id", table_name="ontology_gate_result")
    op.drop_index("ix_ontology_gate_result_decision", table_name="ontology_gate_result")
    op.drop_table("ontology_gate_result")
    op.drop_index("ix_ontology_class_revision_class_id", table_name="ontology_class_revision")
    op.drop_table("ontology_class_revision")
    op.drop_index("ix_ontology_class_parent_parent", table_name="ontology_class_parent")
    op.drop_index("ix_ontology_class_parent_child", table_name="ontology_class_parent")
    op.drop_table("ontology_class_parent")
    op.drop_index("ix_ontology_change_proposal_id", table_name="ontology_change")
    op.drop_index("ix_ontology_change_object_type", table_name="ontology_change")
    op.drop_index("ix_ontology_change_object_id", table_name="ontology_change")
    op.drop_table("ontology_change")
    op.drop_index("ix_ontology_alias_predicate_id", table_name="ontology_alias")
    op.drop_index("ix_ontology_alias_class_id", table_name="ontology_alias")
    op.drop_index("ix_ontology_alias_alias", table_name="ontology_alias")
    op.drop_table("ontology_alias")
    op.drop_index("ix_idempotency_record_request_hash", table_name="idempotency_record")
    op.drop_table("idempotency_record")
    op.drop_index("ix_external_reference_source_system", table_name="external_reference")
    op.drop_index("ix_external_reference_entity_id", table_name="external_reference")
    op.drop_table("external_reference")
    op.drop_index("ix_entity_type_entity_id", table_name="entity_type")
    op.drop_index("ix_entity_type_class_id", table_name="entity_type")
    op.drop_table("entity_type")
    op.drop_index("ix_entity_alias_normalized_alias", table_name="entity_alias")
    op.drop_index("ix_entity_alias_alias", table_name="entity_alias")
    op.drop_table("entity_alias")
    op.drop_index("ix_source_source_system", table_name="source")
    op.drop_index("ix_source_external_id", table_name="source")
    op.drop_index("ix_source_content_hash", table_name="source")
    op.drop_table("source")
    op.drop_index("ix_operation_log_trace_id", table_name="operation_log")
    op.drop_index("ix_operation_log_status", table_name="operation_log")
    op.drop_index("ix_operation_log_request_id", table_name="operation_log")
    op.drop_index("ix_operation_log_operation_name", table_name="operation_log")
    op.drop_index("ix_operation_log_error_code", table_name="operation_log")
    op.drop_index("ix_operation_log_actor_id", table_name="operation_log")
    op.drop_table("operation_log")
    op.drop_index("ix_ontology_proposal_status", table_name="ontology_proposal")
    op.drop_index("ix_ontology_proposal_request_id", table_name="ontology_proposal")
    op.drop_index("ix_ontology_proposal_proposed_by_actor_id", table_name="ontology_proposal")
    op.drop_table("ontology_proposal")
    op.drop_index("ix_ontology_predicate_namespace_id", table_name="ontology_predicate")
    op.drop_index("ix_ontology_predicate_key", table_name="ontology_predicate")
    op.drop_table("ontology_predicate")
    op.drop_index("ix_ontology_constraint_type", table_name="ontology_constraint")
    op.drop_table("ontology_constraint")
    op.drop_index("ix_ontology_class_namespace_id", table_name="ontology_class")
    op.drop_index("ix_ontology_class_key", table_name="ontology_class")
    op.drop_table("ontology_class")
    op.drop_index("ix_ingestion_batch_status", table_name="ingestion_batch")
    op.drop_index("ix_ingestion_batch_actor_id", table_name="ingestion_batch")
    op.drop_table("ingestion_batch")
    op.drop_index("ix_entity_status", table_name="entity")
    op.drop_index("ix_entity_merged_into_entity_id", table_name="entity")
    op.drop_index("ix_entity_canonical_name", table_name="entity")
    op.drop_table("entity")
    op.drop_table("ontology_namespace")
    op.drop_index("ix_actor_status", table_name="actor")
    op.drop_index("ix_actor_actor_type", table_name="actor")
    op.drop_table("actor")
    # ### end Alembic commands ###
