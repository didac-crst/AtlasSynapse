"""Phase 9 ontology read-plane tests."""

from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from semantic_memory.models import AliasTargetType, OntologyAlias, OntologyNamespace
from semantic_memory.seeding import ensure_rich_event_models
from semantic_memory.seeding.ontology import CORE_NAMESPACE_KEY, stable_seed_id
from semantic_memory.services.ontology import OntologyService


def _add_person_alias(session: Session) -> None:
    namespace = session.scalar(
        select(OntologyNamespace).where(OntologyNamespace.key == CORE_NAMESPACE_KEY)
    )
    assert namespace is not None
    person = OntologyService(session).get_class(class_key="Person")
    existing = session.scalar(
        select(OntologyAlias).where(
            OntologyAlias.namespace_id == namespace.id,
            OntologyAlias.alias == "human",
        )
    )
    if existing is not None:
        return
    session.add(
        OntologyAlias(
            id=stable_seed_id("alias", CORE_NAMESPACE_KEY, "human"),
            namespace_id=namespace.id,
            alias="human",
            target_type=AliasTargetType.CLASS.value,
            class_id=person.id,
        )
    )
    session.flush()


def test_get_class_and_inheritance_traversal(db_session: Session) -> None:
    ensure_rich_event_models(db_session)
    service = OntologyService(db_session)
    employment = service.get_class(class_key="Employment")
    assert employment.key == "Employment"
    assert "RelationshipContext" in employment.parent_class_keys
    assert "Thing" in employment.ancestor_class_keys
    thing = service.get_class(class_key="Thing")
    assert "Document" in thing.descendant_class_keys
    assert "Employment" in thing.descendant_class_keys


def test_get_predicate_and_search(db_session: Session) -> None:
    service = OntologyService(db_session)
    predicate = service.get_predicate(predicate_key="hasParticipant")
    assert predicate.cardinality is not None
    assert "Agent" in predicate.range_class_keys
    hits = service.search_ontology(query="participant")
    assert any(hit.key == "hasParticipant" for hit in hits.hits)
    class_hits = service.search_ontology(query="Person")
    assert any(hit.key == "Person" and hit.hit_type.value == "class" for hit in class_hits.hits)


def test_alias_resolution_and_context(db_session: Session) -> None:
    ensure_rich_event_models(db_session)
    _add_person_alias(db_session)
    service = OntologyService(db_session)
    by_alias = service.get_class(alias="human")
    assert by_alias.key == "Person"
    search = service.search_ontology(query="human")
    assert any(hit.hit_type.value == "alias" and hit.key == "Person" for hit in search.hits)
    context = service.get_ontology_context(class_key="Employment")
    assert context.ontology_class.key == "Employment"
    predicate_keys = {item.key for item in context.applicable_predicates}
    assert "hasParticipant" in predicate_keys
    assert "startedAt" in predicate_keys


def test_http_ontology_read_endpoints(client: TestClient) -> None:
    # Use HTTP against the same migrated schema; rich-event classes may be absent.
    person = client.get("/v1/ontology/classes", params={"class_key": "Person"})
    assert person.status_code == 200
    assert person.json()["key"] == "Person"

    predicate = client.get("/v1/ontology/predicates", params={"predicate_key": "relatedTo"})
    assert predicate.status_code == 200
    assert predicate.json()["key"] == "relatedTo"

    search = client.get("/v1/ontology/search", params={"query": "Event"})
    assert search.status_code == 200
    assert search.json()["hits"]

    context = client.get("/v1/ontology/context", params={"class_key": "Document"})
    assert context.status_code == 200
    body = context.json()
    assert body["ontology_class"]["key"] == "Document"
    assert any(item["key"] == "description" for item in body["applicable_predicates"])

    missing = client.get("/v1/ontology/classes", params={"class_key": "NoSuchClass"})
    assert missing.status_code == 404
    assert missing.json()["error_code"] == "UNKNOWN_CLASS"
