"""Resolve one knowledge candidate into a typed plan + blockers (no live writes)."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.orm import Session

from semantic_memory.models.enums import (
    KnowledgeCandidateKind,
    KnowledgeCandidatePolarity,
    ValueKind,
)
from semantic_memory.models.knowledge_ingestion import KnowledgeCandidate
from semantic_memory.repositories.entities import EntityRepository
from semantic_memory.services.knowledge_resolution.identity_plan import (
    plan_object_entity,
    plan_subject,
)
from semantic_memory.services.knowledge_resolution.ontology_plan import (
    plan_predicate_for_claim,
    plan_predicate_for_domain,
)
from semantic_memory.services.knowledge_resolution.routing import (
    claim_epistemic_kind,
    looks_domain_representable,
    route_commit_path,
)
from semantic_memory.services.knowledge_resolution.schemas import (
    BlockerType,
    CandidateResolution,
    CommitPath,
    ObjectBindPlan,
    ProjectionDisposition,
    ResolutionBlocker,
    ResolutionWarning,
    ResolutionWarningCode,
)
from semantic_memory.services.ontology import OntologyService


@dataclass
class CandidateResolveResult:
    resolution: CandidateResolution
    blockers: list[ResolutionBlocker] = field(default_factory=list)
    eligible: bool = False
    ontology_proposal_created: bool = False
    ontology_proposal_reused: bool = False
    identity_blockers: int = 0


def resolve_candidate(
    session: Session,
    candidate: KnowledgeCandidate,
    *,
    actor_key: str,
    dry_run: bool,
    unmet_parent_ids: list[uuid.UUID] | None = None,
    allow_propose: bool = True,
) -> CandidateResolveResult:
    """Produce a typed resolution for one candidate.

    Never creates Entity / Statement / Claim / Evidence.
    Ontology proposals may be created in execute mode only.
    """
    unmet = list(unmet_parent_ids or [])
    if unmet:
        blockers = [
            ResolutionBlocker(
                type=BlockerType.DEPENDENCY,
                field=None,
                ref=str(parent_id),
                detail="waiting_on_parent_candidate",
                required=True,
                parent_candidate_id=parent_id,
            )
            for parent_id in unmet
        ]
        path = route_commit_path(candidate)
        return CandidateResolveResult(
            resolution=CandidateResolution(
                commit_path=path,
                claim_text=candidate.claim_text,
                epistemic_kind=claim_epistemic_kind(candidate),
            ),
            blockers=blockers,
            eligible=False,
        )

    path = route_commit_path(candidate)
    if path == CommitPath.CLAIM:
        return _resolve_claim_path(session, candidate, dry_run=dry_run)

    domain_result = _resolve_domain_path(
        session,
        candidate,
        actor_key=actor_key,
        dry_run=dry_run,
        allow_propose=allow_propose,
    )
    if domain_result.eligible or domain_result.blockers:
        return domain_result

    # Domain path unsafe/incomplete → Claim fallback (preserve epistemic memory).
    fallback = _resolve_claim_path(session, candidate, dry_run=dry_run)
    fallback.resolution.warnings.append(
        ResolutionWarning(
            code=ResolutionWarningCode.CLAIM_FALLBACK_UNSAFE_DOMAIN,
            detail="domain assertion not safely representable; Claim fallback",
            field=None,
        )
    )
    fallback.resolution.commit_path = CommitPath.CLAIM
    return fallback


def _payload(candidate: KnowledgeCandidate) -> dict[str, Any]:
    return candidate.claim_payload if isinstance(candidate.claim_payload, dict) else {}


def _text_from_side(side: Any) -> str | None:
    if isinstance(side, dict):
        text = side.get("text")
        return str(text) if text is not None else None
    if isinstance(side, str):
        return side
    return None


def _resolve_claim_path(
    session: Session,
    candidate: KnowledgeCandidate,
    *,
    dry_run: bool,
) -> CandidateResolveResult:
    del dry_run  # Claim path never proposes ontology.
    payload = _payload(candidate)
    warnings: list[ResolutionWarning] = []
    blockers: list[ResolutionBlocker] = []

    claim_text = candidate.claim_text
    if not claim_text or not str(claim_text).strip():
        blockers.append(
            ResolutionBlocker(
                type=BlockerType.POLICY,
                field="claim_text",
                detail="claim_path_requires_claim_text",
                required=True,
            )
        )
        return CandidateResolveResult(
            resolution=CandidateResolution(
                commit_path=CommitPath.CLAIM,
                claim_text=claim_text,
                epistemic_kind=claim_epistemic_kind(candidate),
            ),
            blockers=blockers,
            eligible=False,
        )

    # Auditable reason when positive assertion never attempted domain path.
    if (
        candidate.kind == KnowledgeCandidateKind.ASSERTION.value
        and candidate.polarity == KnowledgeCandidatePolarity.POSITIVE.value
        and not looks_domain_representable(candidate)
    ):
        warnings.append(
            ResolutionWarning(
                code=ResolutionWarningCode.INSUFFICIENT_STRUCTURE_FOR_DOMAIN_ASSERTION,
                detail="positive assertion lacks safe SPO structure for domain path",
                field=None,
            )
        )

    subject_text = _text_from_side(payload.get("subject"))
    subject_plan, sub_warns, sub_block = plan_subject(
        session, text=subject_text, required=False, adjudicate=False
    )
    warnings.extend(sub_warns)
    del sub_block

    pred_hint = payload.get("predicate_key_hint") or payload.get("predicate_text")
    pred_plan, pred_warns = plan_predicate_for_claim(
        str(pred_hint) if isinstance(pred_hint, str) else None
    )
    warnings.extend(pred_warns)

    object_side = payload.get("object")
    object_text = _text_from_side(object_side)
    if object_text is None and object_side is not None and not isinstance(object_side, dict):
        object_plan = ObjectBindPlan(
            disposition=ProjectionDisposition.LITERAL,
            literal_value=object_side,
            detail="literal_object",
        )
    else:
        object_plan, obj_warns, _obj_block = plan_object_entity(
            session, text=object_text, required=False, adjudicate=False
        )
        warnings.extend(obj_warns)

    resolution = CandidateResolution(
        commit_path=CommitPath.CLAIM,
        subject=subject_plan,
        predicate=pred_plan,
        object=object_plan,
        warnings=warnings,
        claim_text=claim_text,
        epistemic_kind=claim_epistemic_kind(candidate),
    )
    return CandidateResolveResult(resolution=resolution, blockers=[], eligible=True)


def _resolve_domain_path(
    session: Session,
    candidate: KnowledgeCandidate,
    *,
    actor_key: str,
    dry_run: bool,
    allow_propose: bool = True,
) -> CandidateResolveResult:
    payload = _payload(candidate)
    warnings: list[ResolutionWarning] = []
    blockers: list[ResolutionBlocker] = []
    identity_blockers = 0
    proposal_created = False
    proposal_reused = False
    proposal_ids: list[uuid.UUID] = []
    would_propose: list[dict[str, Any]] = []

    subject_text = _text_from_side(payload.get("subject"))
    subject_plan, sub_warns, sub_needs = plan_subject(
        session, text=subject_text, required=True, adjudicate=False
    )
    warnings.extend(sub_warns)
    if sub_needs:
        identity_blockers += 1
        blockers.append(
            ResolutionBlocker(
                type=BlockerType.IDENTITY_CLARIFICATION,
                field="subject",
                ref=subject_text,
                detail=(
                    "required_identity_ambiguous; WriteClarificationRequest not issued "
                    "(needs frozen assert payload — Phase E)"
                ),
                required=True,
            )
        )

    pred_hint_raw = payload.get("predicate_key_hint") or payload.get("predicate_text")
    pred_hint = str(pred_hint_raw).strip() if isinstance(pred_hint_raw, str) else None
    pred_result = plan_predicate_for_domain(
        session,
        hint=pred_hint,
        actor_key=actor_key,
        dry_run=dry_run,
        claim_text=candidate.claim_text,
        allow_propose=allow_propose,
        idempotency_key=f"ki-resolve-pred:{candidate.ingestion_id}:{pred_hint or 'none'}",
    )
    pred_plan = pred_result.plan
    warnings.extend(pred_result.warnings)
    if pred_result.would_propose is not None:
        would_propose.append(pred_result.would_propose)

    if pred_result.policy_block:
        blockers.append(
            ResolutionBlocker(
                type=BlockerType.POLICY,
                field="predicate",
                ref=pred_hint,
                detail=pred_result.policy_detail or "ontology_policy_block",
                required=True,
                ontology_proposal_id=pred_result.proposal_id,
                ontology_clarification_request_id=pred_result.ontology_clarification_request_id,
            )
        )
        resolution = CandidateResolution(
            commit_path=CommitPath.DOMAIN_ASSERTION,
            subject=subject_plan,
            predicate=pred_plan,
            ontology_proposal_ids=(
                [pred_result.proposal_id] if pred_result.proposal_id is not None else []
            ),
            warnings=warnings,
            would_propose_ontology=would_propose,
            claim_text=candidate.claim_text,
            epistemic_kind=claim_epistemic_kind(candidate),
        )
        return CandidateResolveResult(
            resolution=resolution,
            blockers=blockers,
            eligible=False,
            identity_blockers=identity_blockers,
        )

    if pred_plan.disposition == ProjectionDisposition.PROPOSAL:
        if pred_plan.proposal_id is not None:
            proposal_ids.append(pred_plan.proposal_id)
            if pred_plan.detail == "reused_open_proposal":
                proposal_reused = True
            elif pred_result.proposal_outcome not in {None, "OPEN_REUSED", "WOULD_PROPOSE"}:
                proposal_created = True
            detail = "ontology_proposal_pending_apply"
            if pred_result.proposal_outcome == "MANUAL_REVIEW":
                detail = "ontology_proposal_manual_review"
            elif pred_result.proposal_outcome == "READY_TO_APPLY":
                detail = "ontology_proposal_ready_to_apply"
            blockers.append(
                ResolutionBlocker(
                    type=BlockerType.ONTOLOGY_PROPOSAL,
                    field="predicate",
                    ref=pred_hint,
                    detail=detail,
                    required=True,
                    ontology_proposal_id=pred_plan.proposal_id,
                    ontology_clarification_request_id=(
                        pred_result.ontology_clarification_request_id
                    ),
                )
            )
        elif dry_run:
            blockers.append(
                ResolutionBlocker(
                    type=BlockerType.ONTOLOGY_PROPOSAL,
                    field="predicate",
                    ref=pred_hint,
                    detail="would_propose_ontology_dry_run",
                    required=True,
                )
            )
    elif pred_plan.disposition != ProjectionDisposition.REUSE:
        # REJECTED / necessity failure → Claim fallback (no zombie ontology blocker).
        return CandidateResolveResult(
            resolution=CandidateResolution(
                commit_path=CommitPath.DOMAIN_ASSERTION,
                subject=subject_plan,
                predicate=pred_plan,
                warnings=warnings,
                claim_text=candidate.claim_text,
                epistemic_kind=claim_epistemic_kind(candidate),
            ),
            blockers=[],
            eligible=False,
            identity_blockers=identity_blockers,
        )

    object_side = payload.get("object")
    object_text = _text_from_side(object_side)
    object_plan: ObjectBindPlan
    if pred_plan.predicate_key is not None:
        try:
            pred = OntologyService(session).get_predicate(predicate_key=pred_plan.predicate_key)
        except Exception:  # noqa: BLE001
            pred = None
    else:
        pred = None

    if pred is not None and pred.value_kind in {
        ValueKind.STRING,
        ValueKind.NUMBER,
        ValueKind.BOOLEAN,
    }:
        object_plan = ObjectBindPlan(
            disposition=ProjectionDisposition.LITERAL,
            text=object_text,
            literal_value=object_text if object_text is not None else object_side,
            detail="literal_value_kind",
        )
    else:
        object_plan, obj_warns, obj_needs = plan_object_entity(
            session, text=object_text, required=True, adjudicate=False
        )
        warnings.extend(obj_warns)
        if obj_needs:
            identity_blockers += 1
            blockers.append(
                ResolutionBlocker(
                    type=BlockerType.IDENTITY_CLARIFICATION,
                    field="object",
                    ref=object_text,
                    detail=(
                        "required_identity_ambiguous; WriteClarificationRequest not issued "
                        "(needs frozen assert payload — Phase E)"
                    ),
                    required=True,
                )
            )

    if (
        pred is not None
        and subject_plan.disposition == ProjectionDisposition.REUSE
        and subject_plan.entity_id is not None
        and object_plan.disposition == ProjectionDisposition.REUSE
        and object_plan.entity_id is not None
    ):
        if not _domain_range_ok(
            session,
            predicate_key=pred.key,
            subject_id=subject_plan.entity_id,
            object_id=object_plan.entity_id,
            domain_keys=pred.domain_class_keys,
            range_keys=pred.range_class_keys,
        ):
            return CandidateResolveResult(
                resolution=CandidateResolution(
                    commit_path=CommitPath.DOMAIN_ASSERTION,
                    subject=subject_plan,
                    predicate=pred_plan,
                    object=object_plan,
                    warnings=warnings,
                    claim_text=candidate.claim_text,
                ),
                blockers=[],
                eligible=False,
                identity_blockers=identity_blockers,
            )

    resolution = CandidateResolution(
        commit_path=CommitPath.DOMAIN_ASSERTION,
        subject=subject_plan,
        predicate=pred_plan,
        object=object_plan,
        ontology_proposal_ids=proposal_ids,
        warnings=warnings,
        would_propose_ontology=would_propose,
        claim_text=candidate.claim_text,
        epistemic_kind=claim_epistemic_kind(candidate),
    )
    eligible = not blockers
    return CandidateResolveResult(
        resolution=resolution,
        blockers=blockers,
        eligible=eligible,
        ontology_proposal_created=proposal_created,
        ontology_proposal_reused=proposal_reused,
        identity_blockers=identity_blockers,
    )


def _domain_range_ok(
    session: Session,
    *,
    predicate_key: str,
    subject_id: uuid.UUID,
    object_id: uuid.UUID,
    domain_keys: list[str],
    range_keys: list[str],
) -> bool:
    del predicate_key
    entities = EntityRepository(session)
    subject_keys = {cls.key for cls, _ns in entities.list_types(subject_id)}
    object_keys = {cls.key for cls, _ns in entities.list_types(object_id)}
    if domain_keys and "Thing" not in domain_keys and not (subject_keys & set(domain_keys)):
        return False
    if range_keys and "Thing" not in range_keys and not (object_keys & set(range_keys)):
        return False
    return True
