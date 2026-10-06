"""Identity-resolution contracts (PR1).

These types define the identity layer without changing create/resolve behaviour
yet. Legacy ResolutionOutcome (REUSE/CREATE/AMBIGUOUS) remains the public
create_entity response; map via compatibility helpers.
"""

from __future__ import annotations

import uuid
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field, field_validator

from semantic_memory.models.enums import AliasIdentityStrength, EntityStatus
from semantic_memory.schemas.entities import ResolutionOutcome


class CandidateDecision(StrEnum):
    """Per-candidate adjudication (not a global resolver outcome)."""

    SAME = "SAME"
    DIFFERENT = "DIFFERENT"
    UNCERTAIN = "UNCERTAIN"


class IdentityResolutionOutcome(StrEnum):
    """Aggregate identity resolution after candidate adjudication."""

    MATCH = "MATCH"
    NO_MATCH = "NO_MATCH"
    AMBIGUOUS = "AMBIGUOUS"


class IdentityAction(StrEnum):
    """Write action derived from IdentityResolutionOutcome (separate concern)."""

    REUSE = "REUSE"
    CREATE = "CREATE"
    CLARIFY = "CLARIFY"
    REJECT = "REJECT"


class EvidenceStrength(StrEnum):
    """Signal strength. Decisive signals may establish SAME; supporting never alone."""

    DECISIVE = "decisive"
    SUPPORTING = "supporting"


class IdentityEvidence(BaseModel):
    """One identity signal with provenance refs from day one.

    No numeric score field: scores must not directly imply MATCH.
    """

    signal: str = Field(min_length=1)
    strength: EvidenceStrength
    value: str | None = None
    namespace: str | None = None
    predicate: str | None = None
    object_entity_id: uuid.UUID | None = None
    detail: str | None = None
    evidence_refs: list[str] = Field(default_factory=list)

    @field_validator("evidence_refs")
    @classmethod
    def _strip_refs(cls, value: list[str]) -> list[str]:
        return [item.strip() for item in value if item and item.strip()]


class IdentityCandidate(BaseModel):
    entity_id: uuid.UUID
    canonical_name: str
    status: EntityStatus
    class_keys: list[str] = Field(default_factory=list)
    decision: CandidateDecision
    reasons: list[IdentityEvidence] = Field(default_factory=list)


class IdentityResolutionResult(BaseModel):
    """Full identity-resolution payload for future write paths and audit logs."""

    resolution: IdentityResolutionOutcome
    action: IdentityAction
    entity_id: uuid.UUID | None = None
    candidates: list[IdentityCandidate] = Field(default_factory=list)
    reasons: list[IdentityEvidence] = Field(default_factory=list)
    decision_basis: str | None = None
    # Diagnostic only: empty candidate set is one reason for NO_MATCH, not a
    # separate top-level outcome.
    no_candidate: bool = False
    metadata: dict[str, Any] = Field(default_factory=dict)


def action_for_resolution(resolution: IdentityResolutionOutcome) -> IdentityAction:
    if resolution == IdentityResolutionOutcome.MATCH:
        return IdentityAction.REUSE
    if resolution == IdentityResolutionOutcome.NO_MATCH:
        return IdentityAction.CREATE
    return IdentityAction.CLARIFY


def to_legacy_resolution_outcome(
    resolution: IdentityResolutionOutcome,
) -> ResolutionOutcome:
    """Map new identity outcomes onto the existing create_entity enum."""
    if resolution == IdentityResolutionOutcome.MATCH:
        return ResolutionOutcome.REUSE
    if resolution == IdentityResolutionOutcome.NO_MATCH:
        return ResolutionOutcome.CREATE
    return ResolutionOutcome.AMBIGUOUS


def from_legacy_resolution_outcome(
    outcome: ResolutionOutcome,
) -> tuple[IdentityResolutionOutcome, IdentityAction]:
    """Compatibility shim: old create_entity outcomes → new decision + action."""
    if outcome == ResolutionOutcome.REUSE:
        return IdentityResolutionOutcome.MATCH, IdentityAction.REUSE
    if outcome == ResolutionOutcome.CREATE:
        return IdentityResolutionOutcome.NO_MATCH, IdentityAction.CREATE
    return IdentityResolutionOutcome.AMBIGUOUS, IdentityAction.CLARIFY


def aggregate_candidate_decisions(
    candidates: list[IdentityCandidate],
) -> IdentityResolutionOutcome:
    """Derive aggregate resolution from per-candidate decisions.

    Rules:
    - any UNCERTAIN → AMBIGUOUS
    - more than one SAME → AMBIGUOUS
    - exactly one SAME → MATCH
    - otherwise (all DIFFERENT or empty) → NO_MATCH
    """
    if any(item.decision == CandidateDecision.UNCERTAIN for item in candidates):
        return IdentityResolutionOutcome.AMBIGUOUS
    same = [item for item in candidates if item.decision == CandidateDecision.SAME]
    if len(same) > 1:
        return IdentityResolutionOutcome.AMBIGUOUS
    if len(same) == 1:
        return IdentityResolutionOutcome.MATCH
    return IdentityResolutionOutcome.NO_MATCH


__all__ = [
    "AliasIdentityStrength",
    "CandidateDecision",
    "EvidenceStrength",
    "IdentityAction",
    "IdentityCandidate",
    "IdentityEvidence",
    "IdentityResolutionOutcome",
    "IdentityResolutionResult",
    "action_for_resolution",
    "aggregate_candidate_decisions",
    "from_legacy_resolution_outcome",
    "to_legacy_resolution_outcome",
]
