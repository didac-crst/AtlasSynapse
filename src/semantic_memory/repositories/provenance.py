"""Source and statement-evidence persistence."""

from __future__ import annotations

import hashlib
import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from semantic_memory.models import Source, SourceContentRevision, StatementEvidence


def hash_source_content(*parts: str | None) -> str | None:
    """Return a stable content hash when enough source material is present."""
    material = "|".join(part.strip() for part in parts if part and part.strip())
    if not material:
        return None
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def hash_body(content: str) -> str:
    """SHA-256 of UTF-8 body bytes (canonical or original content)."""
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def revision_locator(
    revision_id: uuid.UUID,
    *,
    start: int | None = None,
    end: int | None = None,
    heading: str | None = None,
) -> str:
    """Build a revision-qualified evidence locator."""
    base = f"rev:{revision_id}"
    fragments: list[str] = []
    if heading:
        fragments.append(f"heading:{heading}")
    if start is not None and end is not None:
        fragments.append(f"offsets:{start}-{end}")
    if not fragments:
        return base
    return f"{base}#{'/'.join(fragments)}"


class ProvenanceRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def get_source(self, source_id: uuid.UUID) -> Source | None:
        return self._session.get(Source, source_id)

    def find_source_by_external(self, *, source_system: str, external_id: str) -> Source | None:
        return self._session.scalar(
            select(Source).where(
                Source.source_system == source_system,
                Source.external_id == external_id,
            )
        )

    def find_sources_by_canonical_external(
        self, *, canonical_source_system: str, external_id: str
    ) -> list[Source]:
        return list(
            self._session.scalars(
                select(Source)
                .where(
                    Source.canonical_source_system == canonical_source_system,
                    Source.external_id == external_id,
                )
                .order_by(Source.created_at.asc(), Source.id.asc())
            ).all()
        )

    def find_source_by_content_hash(self, content_hash: str) -> Source | None:
        return self._session.scalar(select(Source).where(Source.content_hash == content_hash))

    def list_sources_for_entity(self, entity_id: uuid.UUID) -> list[Source]:
        return list(
            self._session.scalars(
                select(Source)
                .where(Source.entity_id == entity_id)
                .order_by(Source.created_at.asc())
            ).all()
        )

    def create_source(
        self,
        *,
        created_by_actor_id: uuid.UUID,
        source_system: str | None = None,
        canonical_source_system: str | None = None,
        external_id: str | None = None,
        uri: str | None = None,
        title: str | None = None,
        content_hash: str | None = None,
        reliability: Decimal | None = None,
        retrieved_at: datetime | None = None,
        entity_id: uuid.UUID | None = None,
        metadata_json: dict[str, Any] | None = None,
        identity_conflict: bool = False,
    ) -> Source:
        row = Source(
            id=uuid.uuid4(),
            source_system=source_system,
            canonical_source_system=canonical_source_system,
            identity_conflict=identity_conflict,
            external_id=external_id,
            uri=uri,
            title=title,
            content_hash=content_hash,
            reliability=reliability,
            retrieved_at=retrieved_at,
            entity_id=entity_id,
            metadata_json=metadata_json or {},
            created_by_actor_id=created_by_actor_id,
        )
        self._session.add(row)
        self._session.flush()
        return row

    def set_source_entity_id(self, source: Source, entity_id: uuid.UUID | None) -> Source:
        source.entity_id = entity_id
        self._session.flush()
        return source

    def merge_source_metadata(self, source: Source, metadata: dict[str, Any]) -> Source:
        if not metadata:
            return source
        merged = dict(source.metadata_json or {})
        merged.update(metadata)
        source.metadata_json = merged
        self._session.flush()
        return source

    def acquire_source_lock(self, *, material: str) -> None:
        digest = hashlib.sha256(material.encode("utf-8")).digest()[:8]
        lock_key = int.from_bytes(digest, byteorder="big", signed=False) % (2**63)
        self._session.execute(
            text("SELECT pg_advisory_xact_lock(:lock_key)"),
            {"lock_key": lock_key},
        )

    def get_content_revision(self, revision_id: uuid.UUID) -> SourceContentRevision | None:
        return self._session.get(SourceContentRevision, revision_id)

    def find_content_revision_by_hash(
        self, *, source_id: uuid.UUID, canonical_content_hash: str
    ) -> SourceContentRevision | None:
        return self._session.scalar(
            select(SourceContentRevision).where(
                SourceContentRevision.source_id == source_id,
                SourceContentRevision.canonical_content_hash == canonical_content_hash,
            )
        )

    def latest_content_revision(self, source_id: uuid.UUID) -> SourceContentRevision | None:
        return self._session.scalar(
            select(SourceContentRevision)
            .where(SourceContentRevision.source_id == source_id)
            .order_by(SourceContentRevision.revision_number.desc())
            .limit(1)
        )

    def next_revision_number(self, source_id: uuid.UUID) -> int:
        current = self._session.scalar(
            select(func.coalesce(func.max(SourceContentRevision.revision_number), 0)).where(
                SourceContentRevision.source_id == source_id
            )
        )
        return int(current or 0) + 1

    def create_content_revision(
        self,
        *,
        source_id: uuid.UUID,
        revision_number: int,
        canonical_content: str,
        canonical_format: str,
        canonical_content_hash: str,
        captured_at: datetime,
        created_by_actor_id: uuid.UUID,
        original_content: str | None = None,
        original_format: str | None = None,
        original_content_hash: str | None = None,
        metadata_json: dict[str, Any] | None = None,
    ) -> SourceContentRevision:
        row = SourceContentRevision(
            id=uuid.uuid4(),
            source_id=source_id,
            revision_number=revision_number,
            canonical_content=canonical_content,
            canonical_format=canonical_format,
            canonical_content_hash=canonical_content_hash,
            original_content=original_content,
            original_format=original_format,
            original_content_hash=original_content_hash,
            captured_at=captured_at,
            metadata_json=metadata_json or {},
            created_by_actor_id=created_by_actor_id,
        )
        self._session.add(row)
        self._session.flush()
        return row

    def search_content_revisions(
        self,
        *,
        query: str,
        source_id: uuid.UUID | None = None,
        entity_id: uuid.UUID | None = None,
        limit: int = 20,
    ) -> list[tuple[SourceContentRevision, Source]]:
        pattern = (
            "%" + query.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        )
        stmt = (
            select(SourceContentRevision, Source)
            .join(Source, Source.id == SourceContentRevision.source_id)
            .where(SourceContentRevision.canonical_content.ilike(pattern, escape="\\"))
            .order_by(SourceContentRevision.captured_at.desc())
            .limit(limit)
        )
        if source_id is not None:
            stmt = stmt.where(SourceContentRevision.source_id == source_id)
        if entity_id is not None:
            stmt = stmt.where(Source.entity_id == entity_id)
        return list(self._session.execute(stmt).all())

    def list_evidence_for_statement(self, statement_id: uuid.UUID) -> list[StatementEvidence]:
        return list(
            self._session.scalars(
                select(StatementEvidence)
                .where(StatementEvidence.statement_id == statement_id)
                .order_by(StatementEvidence.created_at.asc())
            ).all()
        )

    def find_evidence(
        self,
        *,
        statement_id: uuid.UUID,
        source_id: uuid.UUID,
        excerpt: str | None,
        locator: str | None,
    ) -> StatementEvidence | None:
        stmt = select(StatementEvidence).where(
            StatementEvidence.statement_id == statement_id,
            StatementEvidence.source_id == source_id,
        )
        if excerpt is None:
            stmt = stmt.where(StatementEvidence.excerpt.is_(None))
        else:
            stmt = stmt.where(StatementEvidence.excerpt == excerpt)
        if locator is None:
            stmt = stmt.where(StatementEvidence.locator.is_(None))
        else:
            stmt = stmt.where(StatementEvidence.locator == locator)
        return self._session.scalar(stmt)

    def create_evidence(
        self,
        *,
        statement_id: uuid.UUID,
        source_id: uuid.UUID,
        asserted_by_actor_id: uuid.UUID,
        excerpt: str | None = None,
        locator: str | None = None,
        extraction_confidence: Decimal | None = None,
    ) -> StatementEvidence:
        row = StatementEvidence(
            id=uuid.uuid4(),
            statement_id=statement_id,
            source_id=source_id,
            excerpt=excerpt,
            locator=locator,
            extraction_confidence=extraction_confidence,
            asserted_by_actor_id=asserted_by_actor_id,
        )
        self._session.add(row)
        self._session.flush()
        return row
