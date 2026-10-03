"""Migration and seed tests."""

from __future__ import annotations

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import Session

from semantic_memory.seeding.ontology import (
    CORE_CLASSES,
    CORE_INHERITANCE,
    CORE_NAMESPACE_KEY,
    CORE_PREDICATES,
    seed_core_ontology,
    stable_seed_id,
)


def test_fresh_database_migrates_from_zero(alembic_cfg: Config) -> None:
    engine = create_engine(alembic_cfg.get_main_option("sqlalchemy.url"), future=True)
    command.downgrade(alembic_cfg, "base")
    inspector = inspect(engine)
    assert "entity" not in inspector.get_table_names()

    command.upgrade(alembic_cfg, "head")
    inspector = inspect(engine)
    required_tables = {
        "ontology_namespace",
        "ontology_class",
        "ontology_class_revision",
        "ontology_class_parent",
        "ontology_predicate",
        "ontology_predicate_revision",
        "ontology_predicate_domain",
        "ontology_predicate_range",
        "ontology_constraint",
        "ontology_alias",
        "entity",
        "entity_type",
        "entity_alias",
        "statement",
        "statement_qualifier",
        "source",
        "statement_evidence",
        "external_reference",
        "ontology_proposal",
        "ontology_gate_result",
        "ontology_change",
        "ingestion_batch",
        "operation_log",
        "idempotency_record",
        "conflict",
        "actor",
    }
    assert required_tables.issubset(set(inspector.get_table_names()))

    with engine.connect() as conn:
        version = conn.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
        assert version == "ca1ee463c33b"
        class_count = conn.execute(
            text(
                "SELECT COUNT(*) FROM ontology_class c "
                "JOIN ontology_namespace n ON n.id = c.namespace_id "
                "WHERE n.key = :key"
            ),
            {"key": CORE_NAMESPACE_KEY},
        ).scalar_one()
        assert class_count == len(CORE_CLASSES)

        namespace_id = conn.execute(
            text("SELECT id FROM ontology_namespace WHERE key = :key"),
            {"key": CORE_NAMESPACE_KEY},
        ).scalar_one()
        actor_id = conn.execute(text("SELECT id FROM actor WHERE name = 'system'")).scalar_one()
        assert namespace_id == stable_seed_id("namespace", CORE_NAMESPACE_KEY)
        assert actor_id == stable_seed_id("actor", "system")
    engine.dispose()


def test_seed_is_deterministic(db_session: Session) -> None:
    first = seed_core_ontology(db_session)
    second = seed_core_ontology(db_session)

    assert first["namespace"] == CORE_NAMESPACE_KEY
    assert first["class_count"] == len(CORE_CLASSES)
    assert first["predicate_count"] == len(CORE_PREDICATES)
    assert second["created"] is False
    assert second["class_count"] == len(CORE_CLASSES)
    assert second["predicate_count"] == len(CORE_PREDICATES)

    class_keys = set(
        db_session.execute(
            text(
                "SELECT c.key FROM ontology_class c "
                "JOIN ontology_namespace n ON n.id = c.namespace_id "
                "WHERE n.key = :key"
            ),
            {"key": CORE_NAMESPACE_KEY},
        ).scalars()
    )
    assert class_keys == set(CORE_CLASSES)

    parent_links = set(
        db_session.execute(
            text(
                "SELECT child.key, parent.key "
                "FROM ontology_class_parent link "
                "JOIN ontology_class child ON child.id = link.child_class_id "
                "JOIN ontology_class parent ON parent.id = link.parent_class_id"
            )
        ).all()
    )
    assert parent_links == set(CORE_INHERITANCE)

    predicate_keys = set(
        db_session.execute(
            text(
                "SELECT p.key FROM ontology_predicate p "
                "JOIN ontology_namespace n ON n.id = p.namespace_id "
                "WHERE n.key = :key"
            ),
            {"key": CORE_NAMESPACE_KEY},
        ).scalars()
    )
    assert predicate_keys == {item[0] for item in CORE_PREDICATES}
