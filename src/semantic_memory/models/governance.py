"""Governance-domain ORM models."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import CheckConstraint, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from semantic_memory.models.base import Base, CreatedAtMixin, TimestampMixin, UUIDPrimaryKeyMixin
from semantic_memory.models.enums import GateDecision, OntologyChangeObjectType, ProposalStatus


class OntologyProposal(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Governed ontology change proposal."""

    __tablename__ = "ontology_proposal"
    __table_args__ = (
        CheckConstraint(
            f"status IN ({', '.join(repr(v.value) for v in ProposalStatus)})",
            name="status",
        ),
        Index("ix_ontology_proposal_status", "status"),
        Index("ix_ontology_proposal_proposed_by_actor_id", "proposed_by_actor_id"),
        Index("ix_ontology_proposal_request_id", "request_id"),
    )

    proposed_by_actor_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("actor.id"),
        nullable=False,
    )
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default=ProposalStatus.DRAFT.value
    )
    proposal_type: Mapped[str] = mapped_column(Text, nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    base_revision_number: Mapped[int | None] = mapped_column(Integer)
    request_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    decision_reason: Mapped[str | None] = mapped_column(Text)


class OntologyGateResult(Base, UUIDPrimaryKeyMixin, CreatedAtMixin):
    """Auditable deterministic/semantic gate result for a proposal."""

    __tablename__ = "ontology_gate_result"
    __table_args__ = (
        CheckConstraint(
            f"decision IN ({', '.join(repr(v.value) for v in GateDecision)})",
            name="decision",
        ),
        UniqueConstraint("proposal_id", "gate_name", name="uq_ontology_gate_result_proposal_gate"),
        Index("ix_ontology_gate_result_proposal_id", "proposal_id"),
        Index("ix_ontology_gate_result_decision", "decision"),
    )

    proposal_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("ontology_proposal.id"),
        nullable=False,
    )
    gate_name: Mapped[str] = mapped_column(Text, nullable=False)
    decision: Mapped[str] = mapped_column(String(32), nullable=False)
    details: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)


class OntologyChange(Base, UUIDPrimaryKeyMixin, CreatedAtMixin):
    """Accepted ontology change record."""

    __tablename__ = "ontology_change"
    __table_args__ = (
        CheckConstraint(
            f"object_type IN ({', '.join(repr(v.value) for v in OntologyChangeObjectType)})",
            name="object_type",
        ),
        Index("ix_ontology_change_proposal_id", "proposal_id"),
        Index("ix_ontology_change_object_type", "object_type"),
        Index("ix_ontology_change_object_id", "object_id"),
    )

    proposal_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("ontology_proposal.id"),
        nullable=False,
    )
    object_type: Mapped[str] = mapped_column(String(32), nullable=False)
    object_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    previous_revision_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    new_revision_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    change_summary: Mapped[str] = mapped_column(Text, nullable=False)
    applied_by_actor_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("actor.id"),
        nullable=False,
    )
