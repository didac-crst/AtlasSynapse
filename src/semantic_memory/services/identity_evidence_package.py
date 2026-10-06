"""Build hard-capped evidence packages for identity LLM adjudication."""

from __future__ import annotations

from semantic_memory.config import Settings, get_settings
from semantic_memory.schemas.identity import IdentityCandidate
from semantic_memory.schemas.identity_adjudication import (
    BoundedIdentityPackage,
    PackagedCandidate,
    PackagedEvidenceItem,
)


def package_identity_evidence(
    *,
    incoming_canonical_name: str,
    namespace_key: str = "core",
    class_key: str | None = None,
    candidates: list[IdentityCandidate],
    settings: Settings | None = None,
) -> BoundedIdentityPackage:
    """Package deterministic + graph reasons for the model.

    Caps candidates and evidence items. Does not invent evidence.
    """
    cfg = settings or get_settings()
    max_candidates = cfg.identity_review_max_candidates
    max_evidence = cfg.identity_review_max_evidence_per_candidate

    truncated_candidates = max(0, len(candidates) - max_candidates)
    selected = candidates[:max_candidates]
    truncated_evidence = 0
    packaged: list[PackagedCandidate] = []

    for index, candidate in enumerate(selected, start=1):
        evidence_rows = list(candidate.reasons)
        if len(evidence_rows) > max_evidence:
            truncated_evidence += len(evidence_rows) - max_evidence
            evidence_rows = evidence_rows[:max_evidence]
        packaged.append(
            PackagedCandidate(
                candidate_id=f"c{index}",
                entity_id=candidate.entity_id,
                canonical_name=candidate.canonical_name,
                class_keys=list(candidate.class_keys),
                evidence=[
                    PackagedEvidenceItem(
                        evidence_id=f"c{index}_e{e_index}",
                        signal=reason.signal,
                        strength=reason.strength.value,
                        value=reason.value,
                        namespace=reason.namespace,
                        predicate=reason.predicate,
                        detail=reason.detail,
                        evidence_refs=list(reason.evidence_refs),
                    )
                    for e_index, reason in enumerate(evidence_rows, start=1)
                ],
            )
        )

    return BoundedIdentityPackage(
        incoming_canonical_name=incoming_canonical_name,
        namespace_key=namespace_key,
        class_key=class_key,
        candidates=packaged,
        truncated_candidates=truncated_candidates,
        truncated_evidence=truncated_evidence,
    )


def prompt_package_dict(package: BoundedIdentityPackage) -> dict:
    """Serialize for the model without leaking internal-only fields beyond entity_id."""
    return {
        "incoming_canonical_name": package.incoming_canonical_name,
        "namespace_key": package.namespace_key,
        "class_key": package.class_key,
        "candidates": [
            {
                "candidate_id": candidate.candidate_id,
                "entity_id": str(candidate.entity_id),
                "canonical_name": candidate.canonical_name,
                "class_keys": candidate.class_keys,
                "evidence": [
                    item.model_dump(mode="json") for item in candidate.evidence
                ],
            }
            for candidate in package.candidates
        ],
        "notes": {
            "truncated_candidates": package.truncated_candidates,
            "truncated_evidence": package.truncated_evidence,
        },
    }
