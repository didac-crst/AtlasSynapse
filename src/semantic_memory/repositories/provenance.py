"""Source and statement-evidence persistence."""

from __future__ import annotations

import hashlib
import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from semantic_memory.models import Source, StatementEvidence


def hash_source_content(*parts: str | None) -> str | None:
    """Return a stable content hash when enough source material is present."""
    material = "|".join(part.strip() for part in parts if part and part.strip())
    if not material:
        return None
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


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

    def find_source_by_content_hash(self, content_hash: str) -> Source | None:
        return self._session.scalar(select(Source).where(Source.content_hash == content_hash))

    def create_source(
        self,
        *,
        created_by_actor_id: uuid.UUID,
        source_system: str | None = None,
        external_id: str | None = None,
        uri: str | None = None,
        title: str | None = None,
        content_hash: str | None = None,
        reliability: Decimal | None = None,
        retrieved_at: datetime | None = None,
        metadata_json: dict[str, Any] | None = None,
    ) -> Source:
        row = Source(
            id=uuid.uuid4(),
            source_system=source_system,
            external_id=external_id,
            uri=uri,
            title=title,
            content_hash=content_hash,
            reliability=reliability,
            retrieved_at=retrieved_at,
            metadata_json=metadata_json or {},
            created_by_actor_id=created_by_actor_id,
        )
        self._session.add(row)
        self._session.flush()
        return row

    def acquire_source_lock(self, *, material: str) -> None:
        digest = hashlib.sha256(material.encode("utf-8")).digest()[:8]
        lock_key = int.from_bytes(digest, byteorder="big", signed=False) % (2**63)
        self._session.execute(
            text("SELECT pg_advisory_xact_lock(:lock_key)"),
            {"lock_key": lock_key},
        )

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
