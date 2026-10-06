"""Core Agent/Place under Thing so Person satisfies Thing-range predicates."""

from __future__ import annotations

import uuid

from sqlalchemy import text
from sqlalchemy.orm import Session

from semantic_memory.models import ActorType
from semantic_memory.schemas.actors import ActorEnsureRequest
from semantic_memory.schemas.entities import CreateEntityRequest
from semantic_memory.schemas.statements import AssertStatementRequest
from semantic_memory.seeding import CORE_INHERITANCE
from semantic_memory.services.actors import ActorService
from semantic_memory.services.entities import EntityService
from semantic_memory.services.statements import StatementService


def test_core_inheritance_includes_agent_and_place_under_thing() -> None:
    assert ("Agent", "Thing") in CORE_INHERITANCE
    assert ("Place", "Thing") in CORE_INHERITANCE


def test_person_satisfies_related_to_thing_range_without_rich_events(
    db_session: Session,
) -> None:
    """relatedTo is Thing→Thing; Person must work via Agent→Thing without rich-event seed."""
    parents = set(
        db_session.execute(
            text(
                "SELECT child.key, parent.key "
                "FROM ontology_class_parent link "
                "JOIN ontology_class child ON child.id = link.child_class_id "
                "JOIN ontology_class parent ON parent.id = link.parent_class_id"
            )
        ).all()
    )
    assert ("Agent", "Thing") in parents
    assert ("Person", "Agent") in parents

    ActorService(db_session).ensure(ActorEnsureRequest(key="writer", actor_type=ActorType.AGENT))
    entities = EntityService(db_session)
    statements = StatementService(db_session)

    person = entities.create_entity(
        CreateEntityRequest(
            actor_key="writer",
            request_id=uuid.uuid4(),
            idempotency_key=str(uuid.uuid4()),
            canonical_name="Inheritance Person",
            class_key="Person",
        )
    )
    profile = entities.create_entity(
        CreateEntityRequest(
            actor_key="writer",
            request_id=uuid.uuid4(),
            idempotency_key=str(uuid.uuid4()),
            canonical_name="Inheritance Profile",
            class_key="Thing",
        )
    )
    assert person.entity is not None and profile.entity is not None

    result = statements.assert_statement(
        AssertStatementRequest(
            actor_key="writer",
            request_id=uuid.uuid4(),
            idempotency_key=str(uuid.uuid4()),
            subject_entity_id=profile.entity.id,
            predicate_key="relatedTo",
            object_entity_id=person.entity.id,
        )
    )
    assert result.statement.status == "asserted"
