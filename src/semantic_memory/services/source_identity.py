"""Source-system registry and provenance identity resolution."""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from semantic_memory.models.provenance import (
    Source,
    SourceIdentityConflict,
    SourceSystemAlias,
    SourceSystemRegistry,
)
from semantic_memory.schemas.identity import (
    EvidenceStrength,
    IdentityAction,
    IdentityEvidence,
    IdentityResolutionOutcome,
    action_for_resolution,
)


def normalize_source_system_key(raw: str) -> str:
    """Deterministic key form for registry lookup and provisional canonicals."""
    collapsed = re.sub(r"[^a-z0-9]+", "_", raw.strip().lower())
    return collapsed.strip("_")


@dataclass(frozen=True, slots=True)
class SourceCandidate:
    source_id: uuid.UUID
    source_system: str | None
    canonical_source_system: str | None
    external_id: str | None
    identity_conflict: bool
    reasons: list[IdentityEvidence] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class SourceResolutionResult:
    resolution: IdentityResolutionOutcome
    action: IdentityAction
    canonical_source_system: str | None
    requested_source_system: str | None
    source: Source | None = None
    candidates: list[SourceCandidate] = field(default_factory=list)
    reasons: list[IdentityEvidence] = field(default_factory=list)
    conflict_id: uuid.UUID | None = None


class SourceSystemRegistryService:
    """Resolve caller-facing source_system strings to canonical keys."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def canonicalize(self, source_system: str | None) -> str | None:
        if source_system is None:
            return None
        raw = source_system.strip()
        if not raw:
            return None
        normalized = normalize_source_system_key(raw)
        if not normalized:
            return None

        by_alias = self._session.scalar(
            select(SourceSystemRegistry.canonical_key)
            .join(
                SourceSystemAlias,
                SourceSystemAlias.registry_id == SourceSystemRegistry.id,
            )
            .where(SourceSystemAlias.normalized_alias == normalized)
        )
        if by_alias is not None:
            return by_alias

        by_key = self._session.scalar(
            select(SourceSystemRegistry.canonical_key).where(
                SourceSystemRegistry.canonical_key == normalized
            )
        )
        if by_key is not None:
            return by_key

        # Provisional canonical: stable key form, not auto-registered.
        return normalized


class SourceIdentityService:
    """Deterministic provenance identity resolution (no LLM)."""

    def __init__(self, session: Session) -> None:
        self._session = session
        self._registry = SourceSystemRegistryService(session)

    def resolve(
        self,
        *,
        source_system: str | None,
        external_id: str | None,
        source_id: uuid.UUID | None = None,
    ) -> SourceResolutionResult:
        if source_id is not None:
            row = self._session.get(Source, source_id)
            if row is None:
                return SourceResolutionResult(
                    resolution=IdentityResolutionOutcome.NO_MATCH,
                    action=IdentityAction.CREATE,
                    canonical_source_system=None,
                    requested_source_system=source_system,
                    reasons=[
                        IdentityEvidence(
                            signal="unknown_source_id",
                            strength=EvidenceStrength.DECISIVE,
                            value=str(source_id),
                        )
                    ],
                )
            return SourceResolutionResult(
                resolution=IdentityResolutionOutcome.MATCH,
                action=IdentityAction.REUSE,
                canonical_source_system=row.canonical_source_system,
                requested_source_system=source_system or row.source_system,
                source=row,
                reasons=[
                    IdentityEvidence(
                        signal="source_id_match",
                        strength=EvidenceStrength.DECISIVE,
                        value=str(source_id),
                        evidence_refs=[f"source:{source_id}"],
                    )
                ],
            )

        canonical = self._registry.canonicalize(source_system)
        if not canonical or not external_id:
            return SourceResolutionResult(
                resolution=IdentityResolutionOutcome.NO_MATCH,
                action=IdentityAction.CREATE,
                canonical_source_system=canonical,
                requested_source_system=source_system,
                reasons=[
                    IdentityEvidence(
                        signal="insufficient_source_identity",
                        strength=EvidenceStrength.SUPPORTING,
                        detail="canonical_source_system and external_id required for MATCH",
                    )
                ],
            )

        matches = list(
            self._session.scalars(
                select(Source)
                .where(
                    Source.canonical_source_system == canonical,
                    Source.external_id == external_id,
                )
                .order_by(Source.created_at.asc(), Source.id.asc())
            ).all()
        )

        reasons = [
            IdentityEvidence(
                signal="same_external_id",
                strength=EvidenceStrength.DECISIVE,
                value=external_id,
            ),
            IdentityEvidence(
                signal="source_system_alias_match",
                strength=EvidenceStrength.DECISIVE,
                namespace=canonical,
                value=source_system,
            ),
        ]

        if not matches:
            return SourceResolutionResult(
                resolution=IdentityResolutionOutcome.NO_MATCH,
                action=IdentityAction.CREATE,
                canonical_source_system=canonical,
                requested_source_system=source_system,
                reasons=reasons,
            )

        if len(matches) == 1 and not matches[0].identity_conflict:
            return SourceResolutionResult(
                resolution=IdentityResolutionOutcome.MATCH,
                action=IdentityAction.REUSE,
                canonical_source_system=canonical,
                requested_source_system=source_system,
                source=matches[0],
                reasons=reasons,
            )

        # Explicit conflict path: never silently pick a survivor for writes.
        conflict = self._session.scalar(
            select(SourceIdentityConflict).where(
                SourceIdentityConflict.canonical_source_system == canonical,
                SourceIdentityConflict.external_id == external_id,
                SourceIdentityConflict.status == "open",
            )
        )
        candidates = [
            SourceCandidate(
                source_id=row.id,
                source_system=row.source_system,
                canonical_source_system=row.canonical_source_system,
                external_id=row.external_id,
                identity_conflict=row.identity_conflict,
                reasons=reasons,
            )
            for row in matches
        ]
        return SourceResolutionResult(
            resolution=IdentityResolutionOutcome.AMBIGUOUS,
            action=IdentityAction.CLARIFY,
            canonical_source_system=canonical,
            requested_source_system=source_system,
            candidates=candidates,
            reasons=reasons
            + [
                IdentityEvidence(
                    signal="source_identity_conflict",
                    strength=EvidenceStrength.DECISIVE,
                    namespace=canonical,
                    value=external_id,
                    detail=(
                        f"{len(matches)} source rows share this identity key; "
                        "resolve source_identity_conflict before reuse/create"
                    ),
                    evidence_refs=[f"source:{row.id}" for row in matches],
                )
            ],
            conflict_id=None if conflict is None else conflict.id,
        )

    def action_for(self, resolution: IdentityResolutionOutcome) -> IdentityAction:
        return action_for_resolution(resolution)
