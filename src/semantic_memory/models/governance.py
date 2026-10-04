"""Governance-domain ORM models."""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from semantic_memory.models.base import Base, CreatedAtMixin, TimestampMixin, UUIDPrimaryKeyMixin
from semantic_memory.models.enums import (
    GateDecision,
    OntologyChangeObjectType,
    ProposalStatus,
    SemanticChallengeStatus,
    SemanticClarificationStatus,
    SemanticReviewStage,
)


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
    effective_semantic_review_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("ontology_semantic_review.id"),
        nullable=True,
    )


class OntologySemanticChallenge(Base, UUIDPrimaryKeyMixin, CreatedAtMixin):
    """Challenge against a negative semantic review decision."""

    __tablename__ = "ontology_semantic_challenge"
    __table_args__ = (
        CheckConstraint(
            f"status IN ({', '.join(repr(v.value) for v in SemanticChallengeStatus)})",
            name="status",
        ),
        Index("ix_ontology_semantic_challenge_proposal_id", "proposal_id"),
    )

    proposal_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("ontology_proposal.id"),
        nullable=False,
    )
    against_review_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("ontology_semantic_review.id"),
        nullable=True,
    )
    challenge_reason: Mapped[str] = mapped_column(Text, nullable=False)
    evidence_refs: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, default=list)
    proposed_revision: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    created_by_actor_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("actor.id"),
        nullable=False,
    )


class OntologySemanticClarificationRequest(Base, UUIDPrimaryKeyMixin, CreatedAtMixin):
    """Referential ask for more semantics from a proposing agent."""

    __tablename__ = "ontology_semantic_clarification_request"
    __table_args__ = (
        CheckConstraint(
            f"status IN ({', '.join(repr(v.value) for v in SemanticClarificationStatus)})",
            name="status",
        ),
        Index("ix_ontology_semantic_clarification_request_proposal_id", "proposal_id"),
        Index("ix_ontology_semantic_clarification_request_review_id", "review_id"),
        Index("ix_ontology_semantic_clarification_request_status", "status"),
    )

    proposal_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("ontology_proposal.id"),
        nullable=False,
    )
    review_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("ontology_semantic_review.id"),
        nullable=False,
    )
    reason_code: Mapped[str] = mapped_column(String(64), nullable=False)
    question: Mapped[str] = mapped_column(Text, nullable=False)
    required_clarification: Mapped[list[Any]] = mapped_column(
        JSONB, nullable=False, default=list
    )
    related_existing_concepts: Mapped[list[Any]] = mapped_column(
        JSONB, nullable=False, default=list
    )
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default=SemanticClarificationStatus.OPEN.value
    )
    supersedes_clarification_request_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("ontology_semantic_clarification_request.id"),
        nullable=True,
    )
    answer_text: Mapped[str | None] = mapped_column(Text)
    evidence_refs: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, default=list)
    answered_by_actor_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("actor.id"),
        nullable=True,
    )
    answered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resulting_review_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("ontology_semantic_review.id"),
        nullable=True,
    )


class OntologySemanticReview(Base, UUIDPrimaryKeyMixin, CreatedAtMixin):
    """Append-only semantic review decision with challenge lineage."""

    __tablename__ = "ontology_semantic_review"
    __table_args__ = (
        CheckConstraint(
            f"review_stage IN ({', '.join(repr(v.value) for v in SemanticReviewStage)})",
            name="review_stage",
        ),
        CheckConstraint(
            "decision IN ("
            "'approve', 'reject', 'manual_review', 'reuse_existing', 'uphold_rejection'"
            ")",
            name="decision",
        ),
        Index("ix_ontology_semantic_review_proposal_id", "proposal_id"),
        Index("ix_ontology_semantic_review_created_at", "created_at"),
    )

    proposal_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("ontology_proposal.id"),
        nullable=False,
    )
    review_stage: Mapped[str] = mapped_column(String(32), nullable=False)
    previous_review_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("ontology_semantic_review.id"),
        nullable=True,
    )
    challenge_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("ontology_semantic_challenge.id"),
        nullable=True,
    )
    provider: Mapped[str] = mapped_column(Text, nullable=False)
    model: Mapped[str] = mapped_column(Text, nullable=False)
    model_version: Mapped[str | None] = mapped_column(Text)
    prompt_template_version: Mapped[str] = mapped_column(Text, nullable=False)
    context_builder_version: Mapped[str] = mapped_column(Text, nullable=False)
    input_hash: Mapped[str] = mapped_column(Text, nullable=False)
    context_concept_keys: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, default=list)
    decision: Mapped[str] = mapped_column(String(32), nullable=False)
    confidence: Mapped[Decimal | None] = mapped_column(Numeric(5, 4))
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    reasons: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, default=list)
    related_existing_concepts: Mapped[list[Any]] = mapped_column(
        JSONB, nullable=False, default=list
    )
    recommended_actions: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, default=list)
    context_sufficient: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    challengeable: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    authoritative: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    previous_decision: Mapped[str | None] = mapped_column(String(32))
    decision_changed: Mapped[bool | None] = mapped_column(Boolean)
    llm_call_log_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("llm_call_log.id"),
        nullable=True,
    )
    details: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)


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
