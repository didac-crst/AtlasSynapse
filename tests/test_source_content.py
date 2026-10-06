"""Source content revision ingest/get/search and Divide and Conquer smoke."""

from __future__ import annotations

import uuid
from decimal import Decimal

from sqlalchemy.orm import Session

from semantic_memory.content.canonicalize import canonicalize_content, hash_content
from semantic_memory.models import ActorType
from semantic_memory.repositories.provenance import revision_locator
from semantic_memory.schemas.actors import ActorEnsureRequest
from semantic_memory.schemas.entities import CreateEntityRequest
from semantic_memory.schemas.provenance import (
    AddEvidenceRequest,
    GetSourceContentRequest,
    IngestSourceContentRequest,
    SearchSourceContentRequest,
    SourceInput,
)
from semantic_memory.schemas.statements import AssertStatementRequest
from semantic_memory.services.actors import ActorService
from semantic_memory.services.entities import EntityService
from semantic_memory.services.provenance import ProvenanceService
from semantic_memory.services.statements import StatementService

DIVIDE_AND_CONQUER_HTML = """<html><body>
<h1>Divide and Conquer</h1>
<p>A short note on breaking hard problems into smaller ones.</p>
<h2>Core idea</h2>
<p>When a problem is too large, partition it, solve the parts, then combine.</p>
</body></html>"""


def _ensure_writer(session: Session, key: str = "writer") -> None:
    ActorService(session).ensure(ActorEnsureRequest(key=key, actor_type=ActorType.AGENT))


def _create_entity(session: Session, *, name: str, class_key: str) -> uuid.UUID:
    result = EntityService(session).create_entity(
        CreateEntityRequest(
            actor_key="writer",
            request_id=uuid.uuid4(),
            idempotency_key=str(uuid.uuid4()),
            canonical_name=name,
            class_key=class_key,
        )
    )
    assert result.entity is not None
    return result.entity.id


def test_canonicalize_html_is_deterministic() -> None:
    html = "<html><body><h1>Title</h1><p>Hello <strong>world</strong>.</p></body></html>"
    first = canonicalize_content(html, "html", "markdown")
    second = canonicalize_content(html, "html", "markdown")
    assert first.canonical_content == second.canonical_content
    assert first.canonical_hash == second.canonical_hash
    assert first.original_hash == hash_content(html)
    assert first.original_content == html
    assert first.canonicalizer == "html_to_markdown"
    assert first.canonicalizer_version == "1"
    assert first.canonical_content.startswith("# Title\n")
    assert "**world**" in first.canonical_content


def test_ingest_hash_dedup_and_new_revision(db_session: Session) -> None:
    _ensure_writer(db_session)
    doc_id = _create_entity(db_session, name="Memo Alpha", class_key="Document")
    provenance = ProvenanceService(db_session)
    body = "# Hello\n\nWorld\n"
    expected = canonicalize_content(body, "markdown", "markdown")

    first = provenance.ingest_source_content(
        IngestSourceContentRequest(
            actor_key="writer",
            request_id=uuid.uuid4(),
            idempotency_key=str(uuid.uuid4()),
            source=SourceInput(
                source_system="fixture",
                external_id="memo-alpha",
                title="Memo Alpha",
            ),
            document_entity_id=doc_id,
            content=body,
            content_format="markdown",
            metadata={"visibility": "internal"},
        )
    )
    assert first.outcome.value == "CREATE"
    assert first.reused is False
    assert first.revision.revision_number == 1
    assert first.revision.canonical_content_hash == expected.canonical_hash
    assert first.revision.original_content == body
    assert first.revision.metadata["canonicalization"]["method"] == "markdown_normalize"
    assert first.source.entity_id == doc_id
    assert first.source.metadata.get("visibility") == "internal"

    same = provenance.ingest_source_content(
        IngestSourceContentRequest(
            actor_key="writer",
            request_id=uuid.uuid4(),
            idempotency_key=str(uuid.uuid4()),
            source=SourceInput(
                source_system="fixture",
                external_id="memo-alpha",
            ),
            document_entity_id=doc_id,
            content=body,
            content_format="markdown",
        )
    )
    assert same.outcome.value == "REUSE"
    assert same.revision.id == first.revision.id
    assert same.revision.revision_number == 1

    changed = provenance.ingest_source_content(
        IngestSourceContentRequest(
            actor_key="writer",
            request_id=uuid.uuid4(),
            idempotency_key=str(uuid.uuid4()),
            source=SourceInput(
                source_system="fixture",
                external_id="memo-alpha",
            ),
            document_entity_id=doc_id,
            content="# Hello\n\nWorld revised\n",
            content_format="markdown",
        )
    )
    assert changed.outcome.value == "CREATE"
    assert changed.revision.revision_number == 2
    assert changed.revision.id != first.revision.id


def test_html_ingest_preserves_original_and_canonicalizes(db_session: Session) -> None:
    _ensure_writer(db_session)
    doc_id = _create_entity(db_session, name="Html Doc", class_key="Document")
    provenance = ProvenanceService(db_session)
    html = "<h1>Title</h1><p>Body</p>"
    expected = canonicalize_content(html, "html", "markdown")

    ingested = provenance.ingest_source_content(
        IngestSourceContentRequest(
            actor_key="writer",
            request_id=uuid.uuid4(),
            idempotency_key=str(uuid.uuid4()),
            source=SourceInput(source_system="confluence", external_id="page-1", title="Html Doc"),
            document_entity_id=doc_id,
            content=html,
            content_format="html",
            canonical_format="markdown",
        )
    )
    assert ingested.revision.original_format == "html"
    assert ingested.revision.original_content == html
    assert ingested.revision.original_content_hash == expected.original_hash
    assert ingested.revision.canonical_content == expected.canonical_content
    assert ingested.revision.canonical_content_hash == expected.canonical_hash
    assert ingested.revision.canonical_content_hash != ingested.revision.original_content_hash
    assert ingested.revision.metadata["canonicalization"] == {
        "method": "html_to_markdown",
        "version": "1",
    }

    # Same HTML from a different client path must reuse the same revision.
    again = provenance.ingest_source_content(
        IngestSourceContentRequest(
            actor_key="writer",
            request_id=uuid.uuid4(),
            idempotency_key=str(uuid.uuid4()),
            source=SourceInput(source_id=ingested.source.id),
            content=html,
            content_format="html",
        )
    )
    assert again.reused is True
    assert again.revision.id == ingested.revision.id

    fetched = provenance.get_source_content(
        GetSourceContentRequest(source_id=ingested.source.id, include_original=True)
    )
    assert fetched.revision.original_content == html

    without_original = provenance.get_source_content(
        GetSourceContentRequest(source_id=ingested.source.id, include_original=False)
    )
    assert without_original.revision.original_content is None


def test_search_and_revision_qualified_evidence(db_session: Session) -> None:
    _ensure_writer(db_session)
    doc_id = _create_entity(db_session, name="Searchable Doc", class_key="Document")
    provenance = ProvenanceService(db_session)

    ingested = provenance.ingest_source_content(
        IngestSourceContentRequest(
            actor_key="writer",
            request_id=uuid.uuid4(),
            idempotency_key=str(uuid.uuid4()),
            source=SourceInput(source_system="notes", external_id="search-1", title="Searchable"),
            document_entity_id=doc_id,
            content="Alpha beta gamma. Partition then combine.",
            content_format="text",
            canonical_format="markdown",
        )
    )

    found = provenance.search_source_content(
        SearchSourceContentRequest(
            query="Partition",
            document_entity_id=doc_id,
            context_chars=10,
        )
    )
    assert len(found.passages) == 1
    passage = found.passages[0]
    assert "Partition" in passage.excerpt
    assert passage.locator.startswith(f"rev:{ingested.revision.id}#")
    assert "offsets:" in passage.locator

    statement = StatementService(db_session).assert_statement(
        AssertStatementRequest(
            actor_key="writer",
            request_id=uuid.uuid4(),
            idempotency_key=str(uuid.uuid4()),
            subject_entity_id=doc_id,
            predicate_key="description",
            object_string="Uses partition then combine",
        )
    )
    locator = revision_locator(
        ingested.revision.id,
        start=passage.start_offset,
        end=passage.end_offset,
    )
    evidence = provenance.add_evidence(
        AddEvidenceRequest(
            actor_key="writer",
            request_id=uuid.uuid4(),
            idempotency_key=str(uuid.uuid4()),
            statement_id=statement.statement.id,
            source=SourceInput(source_id=ingested.source.id),
            excerpt=passage.excerpt,
            locator=locator,
            extraction_confidence=Decimal("0.9"),
        )
    )
    assert evidence.evidence.locator == locator
    explained = provenance.explain_statement(statement.statement.id)
    assert explained.evidence[0].locator == locator


def test_divide_and_conquer_html_ingest(db_session: Session) -> None:
    _ensure_writer(db_session)
    entities = EntityService(db_session)
    statements = StatementService(db_session)
    provenance = ProvenanceService(db_session)

    doc_id = _create_entity(db_session, name="Divide and Conquer", class_key="Document")
    person_id = _create_entity(db_session, name="Didac Fixture", class_key="Person")
    org_id = _create_entity(db_session, name="Airbus Fixture", class_key="Organization")

    authored = statements.assert_statement(
        AssertStatementRequest(
            actor_key="writer",
            request_id=uuid.uuid4(),
            idempotency_key=str(uuid.uuid4()),
            subject_entity_id=doc_id,
            predicate_key="authoredBy",
            object_entity_id=person_id,
        )
    )
    assert authored.statement.predicate_key == "authoredBy"

    pub = statements.assert_statement(
        AssertStatementRequest(
            actor_key="writer",
            request_id=uuid.uuid4(),
            idempotency_key=str(uuid.uuid4()),
            subject_entity_id=doc_id,
            predicate_key="publicationContext",
            object_entity_id=org_id,
        )
    )
    assert pub.statement.predicate_key == "publicationContext"

    ingested = provenance.ingest_source_content(
        IngestSourceContentRequest(
            actor_key="writer",
            request_id=uuid.uuid4(),
            idempotency_key=str(uuid.uuid4()),
            source=SourceInput(
                source_system="portfolio",
                external_id="divide-and-conquer",
                title="Divide and Conquer",
                uri="https://example.test/divide-and-conquer",
            ),
            document_entity_id=doc_id,
            content=DIVIDE_AND_CONQUER_HTML,
            content_format="html",
            metadata={
                "visibility": "internal",
                "published_at": "2026-09",
                "date_precision": "month",
            },
        )
    )
    assert ingested.source.entity_id == doc_id
    assert ingested.source.metadata["visibility"] == "internal"
    assert ingested.revision.original_format == "html"
    assert ingested.revision.original_content == DIVIDE_AND_CONQUER_HTML
    assert "Divide and Conquer" in ingested.revision.canonical_content
    assert "partition" in ingested.revision.canonical_content.casefold()
    assert ingested.revision.metadata["canonicalization"]["method"] == "html_to_markdown"

    # Second channel for same Document entity.
    alt = provenance.ingest_source_content(
        IngestSourceContentRequest(
            actor_key="writer",
            request_id=uuid.uuid4(),
            idempotency_key=str(uuid.uuid4()),
            source=SourceInput(
                source_system="export",
                external_id="divide-and-conquer-md",
                title="Divide and Conquer (export)",
            ),
            document_entity_id=doc_id,
            content=ingested.revision.canonical_content,
            content_format="markdown",
        )
    )
    assert alt.source.id != ingested.source.id
    assert alt.source.entity_id == doc_id

    by_entity = provenance.get_source_content(GetSourceContentRequest(document_entity_id=doc_id))
    assert "partition" in by_entity.revision.canonical_content.casefold()

    hits = provenance.search_source_content(
        SearchSourceContentRequest(query="partition", document_entity_id=doc_id)
    )
    assert hits.passages

    doc = entities.get(doc_id)
    assert doc.canonical_name == "Divide and Conquer"
