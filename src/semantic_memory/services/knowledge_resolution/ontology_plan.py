"""Ontology reuse and necessity-gated proposals (propose ≠ apply)."""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.orm import Session

from semantic_memory.exceptions import UnknownPredicateError, ValidationFailedError
from semantic_memory.models.enums import (
    Cardinality,
    GateDecision,
    ProposalStatus,
    ProposalType,
    ValueKind,
)
from semantic_memory.repositories.governance import GovernanceRepository
from semantic_memory.repositories.ontology import OntologyRepository
from semantic_memory.schemas.ontology import OntologyHitType
from semantic_memory.schemas.proposals import (
    ProposalOutcome,
    ProposePredicateRequest,
    ProposeResponse,
)
from semantic_memory.services.knowledge_resolution.schemas import (
    PredicateBindPlan,
    ProjectionDisposition,
    ResolutionWarning,
    ResolutionWarningCode,
)
from semantic_memory.services.ontology import OntologyService
from semantic_memory.services.proposals import ProposalService
from semantic_memory.validation.normalization import normalize_text

_PREDICATE_KEY_RE = re.compile(r"^[a-z][A-Za-z0-9]*$")


@dataclass
class DomainPredicatePlanResult:
    """Rich predicate plan including proposal-outcome semantics for blockers."""

    plan: PredicateBindPlan
    warnings: list[ResolutionWarning] = field(default_factory=list)
    proposal_id: uuid.UUID | None = None
    would_propose: dict[str, Any] | None = None
    proposal_outcome: str | None = None
    ontology_clarification_request_id: uuid.UUID | None = None
    policy_block: bool = False
    policy_detail: str | None = None
    possible_reuse_keys: list[str] = field(default_factory=list)


def plan_predicate_for_claim(hint: str | None) -> tuple[PredicateBindPlan, list[ResolutionWarning]]:
    """Claim.claimPredicateKey is descriptive — never requires ontology."""
    warnings: list[ResolutionWarning] = []
    if not hint or not hint.strip():
        return PredicateBindPlan(disposition=ProjectionDisposition.OMIT), warnings
    warnings.append(
        ResolutionWarning(
            code=ResolutionWarningCode.PREDICATE_HINT_NON_ASSERTIVE,
            detail="claimPredicateKey is descriptive; no ontology predicate required",
            field="predicate",
        )
    )
    return (
        PredicateBindPlan(
            disposition=ProjectionDisposition.DESCRIPTIVE_HINT,
            predicate_key_hint=hint.strip(),
            detail="claim_descriptive_hint",
        ),
        warnings,
    )


def plan_predicate_for_domain(
    session: Session,
    *,
    hint: str | None,
    actor_key: str,
    dry_run: bool,
    claim_text: str | None,
    allow_propose: bool,
    idempotency_key: str,
) -> DomainPredicatePlanResult:
    """Plan domain predicate binding.

    Automatic reuse is limited to exact/normalized key or exact registered alias.
    Substring/search hits are discovery-only (``possible_reuse``), never bindings.
    """
    warnings: list[ResolutionWarning] = []
    if not hint or not str(hint).strip():
        return DomainPredicatePlanResult(
            plan=PredicateBindPlan(
                disposition=ProjectionDisposition.OMIT, detail="missing_predicate"
            ),
            warnings=warnings,
        )

    cleaned = str(hint).strip()
    ontology = OntologyService(session)

    reused = _try_reuse_predicate(ontology, session, cleaned)
    if reused is not None:
        return DomainPredicatePlanResult(plan=reused, warnings=warnings)

    possible = _discover_possible_reuse(ontology, cleaned)
    if possible:
        warnings.append(
            ResolutionWarning(
                code=ResolutionWarningCode.POSSIBLE_REUSE_DISCOVERY,
                detail=(
                    "substring/search hits are discovery only, not automatic reuse: "
                    + ", ".join(possible)
                ),
                field="predicate",
            )
        )

    if not allow_propose or not _necessity_gate(cleaned, claim_text=claim_text):
        return DomainPredicatePlanResult(
            plan=PredicateBindPlan(
                disposition=ProjectionDisposition.OMIT,
                predicate_key_hint=cleaned,
                detail="necessity_gate_failed_or_not_reusable",
            ),
            warnings=warnings,
            possible_reuse_keys=possible,
        )

    preview = {
        "key": cleaned,
        "value_kind": ValueKind.ENTITY.value,
        "summary": f"Necessity-gated proposal for domain assertion: {cleaned}",
        "possible_reuse_keys": possible,
    }
    if dry_run:
        warnings.append(
            ResolutionWarning(
                code=ResolutionWarningCode.WOULD_PROPOSE_ONTOLOGY,
                detail=f"dry_run would propose predicate '{cleaned}'",
                field="predicate",
            )
        )
        return DomainPredicatePlanResult(
            plan=PredicateBindPlan(
                disposition=ProjectionDisposition.PROPOSAL,
                predicate_key_hint=cleaned,
                detail="would_propose_ontology",
            ),
            warnings=warnings,
            would_propose=preview,
            proposal_outcome="WOULD_PROPOSE",
            possible_reuse_keys=possible,
        )

    existing_open = _find_open_predicate_proposal(session, cleaned)
    if existing_open is not None:
        return DomainPredicatePlanResult(
            plan=PredicateBindPlan(
                disposition=ProjectionDisposition.PROPOSAL,
                predicate_key_hint=cleaned,
                proposal_id=existing_open,
                detail="reused_open_proposal",
            ),
            warnings=warnings,
            proposal_id=existing_open,
            proposal_outcome="OPEN_REUSED",
            possible_reuse_keys=possible,
        )

    response = ProposalService(session).propose_predicate(
        ProposePredicateRequest(
            actor_key=actor_key,
            request_id=uuid.uuid4(),
            idempotency_key=idempotency_key,
            key=cleaned,
            value_kind=ValueKind.ENTITY,
            cardinality=Cardinality.MANY,
            domain_keys=["Thing"],
            range_keys=["Thing"],
            description=claim_text or cleaned,
            summary=f"Ingestion necessity-gated predicate: {cleaned}",
            metadata={"source": "knowledge_ingestion_resolution"},
        )
    )
    return _interpret_propose_response(
        session,
        ontology=ontology,
        cleaned=cleaned,
        response=response,
        warnings=warnings,
        possible=possible,
    )


def predicate_now_exists(session: Session, key: str) -> PredicateBindPlan | None:
    return _try_reuse_predicate(OntologyService(session), session, key)


def _interpret_propose_response(
    session: Session,
    *,
    ontology: OntologyService,
    cleaned: str,
    response: ProposeResponse,
    warnings: list[ResolutionWarning],
    possible: list[str],
) -> DomainPredicatePlanResult:
    outcome = response.outcome
    clarification_id = (
        None
        if response.open_clarification_request is None
        else response.open_clarification_request.clarification_request_id
    )

    if outcome == ProposalOutcome.REUSE_RECOMMENDED:
        recommended = _recommended_existing_keys(response)
        if len(recommended) == 1:
            reused = _try_reuse_predicate(ontology, session, recommended[0])
            if reused is not None:
                warnings.append(
                    ResolutionWarning(
                        code=ResolutionWarningCode.REUSE_RECOMMENDED,
                        detail=f"proposal recommended unambiguous reuse of '{recommended[0]}'",
                        field="predicate",
                    )
                )
                return DomainPredicatePlanResult(
                    plan=reused,
                    warnings=warnings,
                    proposal_outcome=outcome.value,
                    possible_reuse_keys=possible,
                )
        return DomainPredicatePlanResult(
            plan=PredicateBindPlan(
                disposition=ProjectionDisposition.OMIT,
                predicate_key_hint=cleaned,
                proposal_id=response.proposal.id,
                detail="reuse_recommended_ambiguous",
            ),
            warnings=warnings,
            proposal_id=response.proposal.id,
            proposal_outcome=outcome.value,
            policy_block=True,
            policy_detail=(
                "REUSE_RECOMMENDED without unambiguous existing concept; "
                f"candidates={recommended or possible}"
            ),
            possible_reuse_keys=sorted(set(possible) | set(recommended)),
        )

    if outcome == ProposalOutcome.REJECTED:
        # Never wait on a rejected proposal. Claim fallback is preferred upstream.
        return DomainPredicatePlanResult(
            plan=PredicateBindPlan(
                disposition=ProjectionDisposition.OMIT,
                predicate_key_hint=cleaned,
                proposal_id=response.proposal.id,
                detail=f"proposal_rejected:{response.proposal.decision_reason or 'rejected'}",
            ),
            warnings=warnings,
            proposal_id=response.proposal.id,
            proposal_outcome=outcome.value,
            possible_reuse_keys=possible,
        )

    if outcome == ProposalOutcome.MANUAL_REVIEW:
        return DomainPredicatePlanResult(
            plan=PredicateBindPlan(
                disposition=ProjectionDisposition.PROPOSAL,
                predicate_key_hint=cleaned,
                proposal_id=response.proposal.id,
                detail="proposal_manual_review",
            ),
            warnings=warnings,
            proposal_id=response.proposal.id,
            proposal_outcome=outcome.value,
            ontology_clarification_request_id=clarification_id,
            possible_reuse_keys=possible,
        )

    if outcome == ProposalOutcome.READY_TO_APPLY:
        return DomainPredicatePlanResult(
            plan=PredicateBindPlan(
                disposition=ProjectionDisposition.PROPOSAL,
                predicate_key_hint=cleaned,
                proposal_id=response.proposal.id,
                detail="proposal_ready_to_apply",
            ),
            warnings=warnings,
            proposal_id=response.proposal.id,
            proposal_outcome=outcome.value,
            possible_reuse_keys=possible,
        )

    # Unexpected outcomes — treat as policy, never zombie-wait.
    return DomainPredicatePlanResult(
        plan=PredicateBindPlan(
            disposition=ProjectionDisposition.OMIT,
            predicate_key_hint=cleaned,
            proposal_id=response.proposal.id,
            detail=f"proposal_outcome_unhandled:{outcome.value}",
        ),
        warnings=warnings,
        proposal_id=response.proposal.id,
        proposal_outcome=outcome.value,
        policy_block=True,
        policy_detail=f"Unhandled proposal outcome {outcome.value}",
        possible_reuse_keys=possible,
    )


def _recommended_existing_keys(response: ProposeResponse) -> list[str]:
    keys: list[str] = []
    for gate in response.proposal.gate_results:
        if gate.decision != GateDecision.REUSE_RECOMMENDED:
            continue
        existing = gate.details.get("existing_key")
        if existing:
            keys.append(str(existing))
    review = response.proposal.effective_semantic_review
    if review is not None:
        for related in review.related_existing_concepts:
            if related.key:
                keys.append(str(related.key))
    # Preserve order, unique.
    seen: set[str] = set()
    out: list[str] = []
    for key in keys:
        if key not in seen:
            seen.add(key)
            out.append(key)
    return out


def _try_reuse_predicate(
    ontology: OntologyService, session: Session, key: str
) -> PredicateBindPlan | None:
    """Exact key, normalized-key equality, or exact registered alias only."""
    try:
        pred = ontology.get_predicate(predicate_key=key, namespace_key="core")
        return PredicateBindPlan(
            disposition=ProjectionDisposition.REUSE,
            predicate_key_hint=key,
            predicate_id=pred.id,
            predicate_key=pred.key,
            detail="exact_key_reuse",
        )
    except (UnknownPredicateError, ValidationFailedError):
        pass

    # Normalized-key equality (not substring): "RelatedTo" → relatedTo.
    normalized = OntologyRepository(session).find_predicate_by_normalized_key(
        namespace_key="core", predicate_key=key
    )
    if normalized is not None and not normalized.is_deprecated:
        if normalize_text(normalized.key) == normalize_text(key):
            try:
                pred = ontology.get_predicate(predicate_id=normalized.id)
                return PredicateBindPlan(
                    disposition=ProjectionDisposition.REUSE,
                    predicate_key_hint=key,
                    predicate_id=pred.id,
                    predicate_key=pred.key,
                    detail="normalized_key_reuse",
                )
            except (UnknownPredicateError, ValidationFailedError):
                pass

    try:
        pred = ontology.get_predicate(alias=key, namespace_key="core")
        return PredicateBindPlan(
            disposition=ProjectionDisposition.REUSE,
            predicate_key_hint=key,
            predicate_id=pred.id,
            predicate_key=pred.key,
            detail="exact_alias_reuse",
        )
    except (UnknownPredicateError, ValidationFailedError):
        return None


def _discover_possible_reuse(ontology: OntologyService, key: str) -> list[str]:
    """ILIKE search hits — discovery only, never automatic bind."""
    try:
        results = ontology.search_ontology(query=key, namespace_key="core", limit=10)
    except ValidationFailedError:
        return []
    found: list[str] = []
    seen: set[str] = set()
    key_cf = key.casefold()
    for hit in results.hits:
        if hit.hit_type not in {OntologyHitType.PREDICATE, OntologyHitType.ALIAS}:
            continue
        hit_key = str(hit.key)
        if hit_key.casefold() == key_cf:
            continue  # exact already handled upstream
        if hit_key not in seen:
            seen.add(hit_key)
            found.append(hit_key)
    return found


def _necessity_gate(key: str, *, claim_text: str | None) -> bool:
    """Genuine reusable gap — not vague one-off wording."""
    if not _PREDICATE_KEY_RE.fullmatch(key):
        return False
    if len(key) < 4:
        return False
    if not claim_text or len(claim_text.strip()) < 12:
        return False
    if key.casefold() in {"is", "has", "uses", "does", "should", "shouldnot"}:
        return False
    return True


def _find_open_predicate_proposal(session: Session, key: str) -> uuid.UUID | None:
    """Open / in-review proposals only — never rejected or already-accepted."""
    gov = GovernanceRepository(session)
    for status in (
        ProposalStatus.SUBMITTED.value,
        ProposalStatus.IN_REVIEW.value,
        ProposalStatus.DRAFT.value,
    ):
        rows = gov.list_proposals(
            status=status,
            proposal_type=ProposalType.PREDICATE.value,
            limit=100,
        )
        for row in rows:
            payload = row.payload if isinstance(row.payload, dict) else {}
            if str(payload.get("key", "")).casefold() == key.casefold():
                return row.id
    return None
