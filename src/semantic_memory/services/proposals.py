"""Governed ontology proposal and apply services."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from semantic_memory.config import Settings, get_settings
from semantic_memory.exceptions import (
    ClarificationRequestAlreadyResolvedError,
    ClarificationRequestNotFoundError,
    ClarificationRequestSupersededError,
    OntologyCycleError,
    OntologyProposalRejectedError,
    OntologyReuseRecommendedError,
    RevisionConflictError,
    UnknownClassError,
    UnknownPredicateError,
    UnknownProposalError,
    ValidationFailedError,
)
from semantic_memory.models import (
    AliasTargetType,
    Cardinality,
    OntologyAlias,
    OntologyChangeObjectType,
    OntologyClass,
    OntologyClassParent,
    OntologyClassRevision,
    OntologyConstraint,
    OntologyNamespace,
    OntologyPredicate,
    OntologyPredicateDomain,
    OntologyPredicateRange,
    OntologyPredicateRevision,
    OntologyProposal,
    OperationLog,
    ProposalStatus,
)
from semantic_memory.models.capabilities import Capability
from semantic_memory.models.enums import (
    GateDecision,
    ProposalType,
    SemanticChallengeStatus,
    SemanticClarificationStatus,
    SemanticReviewStage,
)
from semantic_memory.models.governance import (
    OntologySemanticClarificationRequest,
    OntologySemanticReview,
)
from semantic_memory.repositories.governance import GovernanceRepository
from semantic_memory.repositories.ontology import OntologyRepository
from semantic_memory.schemas.proposals import (
    ApplyProposalRequest,
    ApplyProposalResponse,
    GateResultResponse,
    OntologyChangeResponse,
    ProposalOutcome,
    ProposalResponse,
    ProposeAliasRequest,
    ProposeClassParentRequest,
    ProposeClassRequest,
    ProposeConstraintRequest,
    ProposePredicateRequest,
    ProposeResponse,
)
from semantic_memory.schemas.semantic_review import (
    CLARIFICATION_REASON_CODE,
    AnswerSemanticClarificationRequest,
    AnswerSemanticClarificationResponse,
    ChallengeOntologyReviewRequest,
    ChallengeOntologyReviewResponse,
    ClarificationRequestResponse,
    RelatedExistingConcept,
    ReviewReason,
    SemanticDecision,
    SemanticReviewResponse,
    StructuredReviewResult,
)
from semantic_memory.services.actors import ActorService
from semantic_memory.services.embedding_providers import EmbeddingProvider
from semantic_memory.services.embeddings import EmbeddingService
from semantic_memory.services.gates import DeterministicGatePipeline
from semantic_memory.services.llm_logging import DatabaseLlmCallLogger
from semantic_memory.services.mutations import MutationRunner
from semantic_memory.services.openai_reviewer import PROMPT_TEMPLATE_VERSION
from semantic_memory.services.review import (
    LoggingSemanticReviewer,
    ReviewDecision,
    ReviewRequest,
    SemanticReviewer,
    apply_confidence_policy,
    build_semantic_reviewer,
    default_clarification_asks,
    make_semantic_review_gate,
    review_decision_to_gate,
)
from semantic_memory.services.review_context import (
    CONTEXT_BUILDER_VERSION,
    SemanticReviewContextBuilder,
    lexical_similarity_candidates,
)
from semantic_memory.services.review_metrics import SEMANTIC_REVIEW_METRICS


class ProposalService:
    def __init__(
        self,
        session: Session,
        *,
        settings: Settings | None = None,
        reviewer: SemanticReviewer | None = None,
        embedding_provider: EmbeddingProvider | None = None,
    ) -> None:
        self._session = session
        self._settings = settings or get_settings()
        self._actors = ActorService(session, settings=self._settings)
        self._governance = GovernanceRepository(session)
        self._ontology = OntologyRepository(session)
        self._gates = DeterministicGatePipeline(session)
        self._mutations = MutationRunner(session, settings=self._settings)
        self._llm_logger = DatabaseLlmCallLogger(session, self._settings)
        self._review_context: dict[str, Any] = {}
        if reviewer is None:
            self._reviewer = build_semantic_reviewer(self._settings, logger=self._llm_logger)
        elif isinstance(reviewer, LoggingSemanticReviewer):
            self._reviewer = reviewer
        else:
            self._reviewer = LoggingSemanticReviewer(reviewer, self._llm_logger)
        self._embeddings = EmbeddingService(
            session,
            settings=self._settings,
            provider=embedding_provider,
        )
        # Gate order: … → similarity → semantic review → final_deterministic.
        self._gates.register_extra_gate(self._embeddings.make_similarity_gate())
        self._gates.register_extra_gate(
            make_semantic_review_gate(self._reviewer, context_provider=lambda: self._review_context)
        )

    @property
    def gate_pipeline(self) -> DeterministicGatePipeline:
        return self._gates

    def get_proposal(self, proposal_id: uuid.UUID) -> ProposalResponse:
        proposal = self._governance.get_proposal(proposal_id)
        if proposal is None:
            raise UnknownProposalError(
                f"Proposal {proposal_id} was not found",
                details={"proposal_id": str(proposal_id)},
            )
        return self._to_proposal_response(proposal)

    def propose_class(self, request: ProposeClassRequest) -> ProposeResponse:
        payload = {
            "namespace_key": request.namespace_key,
            "key": request.key,
            "label": request.label or request.key,
            "description": request.description,
            "parent_keys": list(request.parent_keys),
            "metadata": request.metadata,
        }
        summary = request.summary or f"Propose class {request.namespace_key}:{request.key}"
        return self._propose(
            request=request,
            proposal_type=ProposalType.CLASS,
            summary=summary,
            payload=payload,
            base_revision_number=None,
        )

    def propose_predicate(self, request: ProposePredicateRequest) -> ProposeResponse:
        payload = {
            "namespace_key": request.namespace_key,
            "key": request.key,
            "label": request.label or request.key,
            "description": request.description,
            "value_kind": request.value_kind.value,
            "datatype": request.datatype,
            "cardinality": request.cardinality.value,
            "domain_keys": list(request.domain_keys),
            "range_keys": list(request.range_keys),
            "is_symmetric": request.is_symmetric,
            "is_transitive": request.is_transitive,
            "metadata": request.metadata,
            "base_revision_number": request.base_revision_number,
        }
        summary = request.summary or f"Propose predicate {request.namespace_key}:{request.key}"
        return self._propose(
            request=request,
            proposal_type=ProposalType.PREDICATE,
            summary=summary,
            payload=payload,
            base_revision_number=request.base_revision_number,
        )

    def propose_constraint(self, request: ProposeConstraintRequest) -> ProposeResponse:
        payload = {
            "namespace_key": request.namespace_key,
            "key": request.key,
            "constraint_type": request.constraint_type.value,
            "expression": request.expression,
            "description": request.description,
        }
        summary = request.summary or f"Propose constraint {request.namespace_key}:{request.key}"
        return self._propose(
            request=request,
            proposal_type=ProposalType.CONSTRAINT,
            summary=summary,
            payload=payload,
            base_revision_number=None,
        )

    def propose_alias(self, request: ProposeAliasRequest) -> ProposeResponse:
        payload = {
            "namespace_key": request.namespace_key,
            "alias": request.alias,
            "target_type": request.target_type.value,
            "target_key": request.target_key,
        }
        summary = request.summary or f"Propose alias {request.alias}"
        return self._propose(
            request=request,
            proposal_type=ProposalType.ALIAS,
            summary=summary,
            payload=payload,
            base_revision_number=None,
        )

    def propose_class_parent(self, request: ProposeClassParentRequest) -> ProposeResponse:
        payload = {
            "namespace_key": request.namespace_key,
            "child_key": request.child_key,
            "parent_key": request.parent_key,
            "base_revision_number": request.base_revision_number,
        }
        summary = request.summary or f"Propose parent {request.parent_key} for {request.child_key}"
        return self._propose(
            request=request,
            proposal_type=ProposalType.CLASS_PARENT,
            summary=summary,
            payload=payload,
            base_revision_number=request.base_revision_number,
        )

    def apply_proposal(self, request: ApplyProposalRequest) -> ApplyProposalResponse:
        actor = self._actors.require_active_actor(request.actor_key)
        self._actors.require_capability(actor, Capability.ONTOLOGY_APPLY)
        return self._mutations.run(
            actor=actor,
            operation_name="apply_proposal",
            request=request,
            response_model=ApplyProposalResponse,
            constraint_name="ontology_apply",
            execute=lambda: self._apply_body(request=request, actor_id=actor.id),
        )

    def _propose(
        self,
        *,
        request: Any,
        proposal_type: ProposalType,
        summary: str,
        payload: dict[str, Any],
        base_revision_number: int | None,
    ) -> ProposeResponse:
        actor = self._actors.require_active_actor(request.actor_key)
        self._actors.require_capability(actor, Capability.ONTOLOGY_PROPOSE)
        return self._mutations.run(
            actor=actor,
            operation_name=f"propose_{proposal_type.value}",
            request=request,
            response_model=ProposeResponse,
            constraint_name="ontology_propose",
            execute=lambda: self._propose_body(
                request_id=request.request_id,
                actor_id=actor.id,
                proposal_type=proposal_type,
                summary=summary,
                payload=payload,
                base_revision_number=base_revision_number,
            ),
        )

    def _propose_body(
        self,
        *,
        request_id: uuid.UUID,
        actor_id: uuid.UUID,
        proposal_type: ProposalType,
        summary: str,
        payload: dict[str, Any],
        base_revision_number: int | None,
    ) -> ProposeResponse:
        proposal = self._governance.create_proposal(
            proposed_by_actor_id=actor_id,
            proposal_type=proposal_type.value,
            summary=summary,
            payload=payload,
            request_id=request_id,
            base_revision_number=base_revision_number,
            status=ProposalStatus.SUBMITTED,
        )
        operation = self._session.scalar(
            select(OperationLog)
            .where(OperationLog.request_id == request_id)
            .order_by(OperationLog.started_at.desc())
            .limit(1)
        )
        review_context = SemanticReviewContextBuilder(self._session, self._settings).build(
            proposal_type=proposal_type,
            payload=payload,
        )
        lexical = lexical_similarity_candidates(
            self._session, proposal_type=proposal_type, payload=payload, limit=5
        )
        self._review_context = {
            "actor_id": actor_id,
            "request_id": request_id,
            "operation_log_id": None if operation is None else operation.id,
            "trace_id": None if operation is None else operation.trace_id,
            "review_context": review_context,
            "lexical_candidates": lexical,
            "settings": self._settings,
        }
        try:
            outcomes = self._gates.run(
                proposal_id=proposal.id,
                proposal_type=proposal_type,
                payload=payload,
            )
        finally:
            self._review_context = {}
        provider = str(
            getattr(self._reviewer, "_provider", getattr(self._reviewer, "provider", "unknown"))
        )
        model = str(getattr(self._reviewer, "_model", getattr(self._reviewer, "model", "unknown")))
        self._persist_initial_semantic_review(
            proposal=proposal,
            outcomes=outcomes,
            provider=provider,
            model=model,
        )
        outcome, status, reason = self._aggregate(outcomes, proposal=proposal)
        self._governance.set_proposal_status(proposal, status=status, decision_reason=reason)
        return ProposeResponse(
            outcome=outcome,
            proposal=self._to_proposal_response(proposal),
            open_clarification_request=self._open_clarification_response(proposal.id),
            request_id=request_id,
        )

    def _aggregate(
        self,
        outcomes: list[Any],
        *,
        proposal: OntologyProposal | None = None,
    ) -> tuple[ProposalOutcome, ProposalStatus, str | None]:
        # Historical semantic_review gate rows stay append-only; effective review wins.
        effective_decision = self._effective_semantic_gate_decision(proposal)
        non_semantic = [item for item in outcomes if item.gate_name != "semantic_review"]
        semantic_gate = next(
            (item for item in outcomes if item.gate_name == "semantic_review"), None
        )
        semantic_decision = effective_decision
        if semantic_decision is None and semantic_gate is not None:
            semantic_decision = semantic_gate.decision

        failed = [
            item.gate_name for item in non_semantic if item.decision == GateDecision.FAIL
        ]
        if semantic_decision == GateDecision.FAIL:
            failed.append("semantic_review")
        if failed:
            return (
                ProposalOutcome.REJECTED,
                ProposalStatus.REJECTED,
                f"Failed gates: {', '.join(failed)}",
            )
        if semantic_decision == GateDecision.REUSE_RECOMMENDED or any(
            item.decision == GateDecision.REUSE_RECOMMENDED for item in non_semantic
        ):
            return (
                ProposalOutcome.REUSE_RECOMMENDED,
                ProposalStatus.REJECTED,
                "Reuse an existing ontology concept",
            )
        if semantic_decision == GateDecision.MANUAL_REVIEW or any(
            item.decision == GateDecision.MANUAL_REVIEW for item in non_semantic
        ):
            return (
                ProposalOutcome.MANUAL_REVIEW,
                ProposalStatus.IN_REVIEW,
                "Semantic review required",
            )
        return ProposalOutcome.READY_TO_APPLY, ProposalStatus.SUBMITTED, None

    def _effective_semantic_gate_decision(
        self, proposal: OntologyProposal | None
    ) -> GateDecision | None:
        if proposal is None or proposal.effective_semantic_review_id is None:
            return None
        review = self._governance.get_semantic_review(proposal.effective_semantic_review_id)
        if review is None:
            return None
        # Shadow / non-authoritative reviews never bind proposal outcomes.
        if not review.authoritative:
            return GateDecision.MANUAL_REVIEW
        return review_decision_to_gate(ReviewDecision(review.decision))

    def _persist_initial_semantic_review(
        self,
        *,
        proposal: OntologyProposal,
        outcomes: list[Any],
        provider: str,
        model: str,
    ) -> None:
        semantic = next((item for item in outcomes if item.gate_name == "semantic_review"), None)
        if semantic is None or (semantic.details or {}).get("skipped"):
            return
        details = semantic.details or {}
        model_decision = str(
            details.get("model_decision")
            or details.get("decision")
            or details.get("review_decision")
            or "manual_review"
        )
        authoritative = bool(details.get("authoritative", True)) and not bool(
            details.get("shadow")
        )
        review = self._governance.create_semantic_review(
            proposal_id=proposal.id,
            review_stage=SemanticReviewStage.INITIAL,
            provider=provider,
            model=model,
            model_version=self._settings.semantic_review_model_version,
            prompt_template_version=str(
                details.get("prompt_template_version") or PROMPT_TEMPLATE_VERSION
            ),
            context_builder_version=str(
                details.get("context_builder_version") or CONTEXT_BUILDER_VERSION
            ),
            input_hash=str(details.get("input_hash") or ""),
            context_concept_keys=list(details.get("context_concept_keys") or []),
            decision=model_decision,
            confidence=details.get("confidence"),
            summary=str(details.get("summary") or details.get("reason") or ""),
            reasons=list(details.get("reasons") or []),
            related_existing_concepts=list(details.get("related_existing_concepts") or []),
            recommended_actions=list(details.get("recommended_actions") or []),
            context_sufficient=bool(details.get("context_sufficient", True)),
            challengeable=bool(details.get("challengeable", True)),
            authoritative=authoritative,
            llm_call_log_id=_optional_uuid(details.get("llm_call_log_id")),
            details={
                "gate_decision": semantic.decision.value,
                "shadow": bool(details.get("shadow")),
                "model_decision": model_decision,
                "candidate_selection_trace": list(
                    details.get("candidate_selection_trace") or []
                ),
                "required_clarification": list(
                    details.get("required_clarification") or []
                ),
            },
        )
        self._governance.set_effective_semantic_review(proposal, review.id)
        clarification = self._maybe_issue_clarification_request(proposal=proposal, review=review)
        details["review_id"] = str(review.id)
        if clarification is not None:
            details["clarification_request_id"] = str(clarification.id)
            details["reason_code"] = clarification.reason_code
            details["required_clarification"] = list(
                clarification.required_clarification or []
            )
        # Enrich historical gate details with review_id pointer only; decision stays unchanged.
        self._governance.upsert_gate_result(
            proposal_id=proposal.id,
            gate_name="semantic_review",
            decision=semantic.decision,
            details=details,
        )

    def challenge_ontology_review(
        self, request: ChallengeOntologyReviewRequest
    ) -> ChallengeOntologyReviewResponse:
        actor = self._actors.require_active_actor(request.actor_key)
        self._actors.require_capability(actor, Capability.ONTOLOGY_PROPOSE)
        return self._mutations.run(
            actor=actor,
            operation_name="challenge_ontology_review",
            request=request,
            response_model=ChallengeOntologyReviewResponse,
            constraint_name="ontology_propose",
            execute=lambda: self._challenge_body(request=request, actor_id=actor.id),
        )

    def _challenge_body(
        self, *, request: ChallengeOntologyReviewRequest, actor_id: uuid.UUID
    ) -> ChallengeOntologyReviewResponse:
        proposal = self._governance.get_proposal(request.proposal_id)
        if proposal is None:
            raise UnknownProposalError(
                f"Proposal {request.proposal_id} was not found",
                details={"proposal_id": str(request.proposal_id)},
                request_id=str(request.request_id),
            )
        if proposal.status not in {
            ProposalStatus.REJECTED.value,
            ProposalStatus.IN_REVIEW.value,
        }:
            raise ValidationFailedError(
                "Only rejected or in-review proposals can be challenged",
                details={"status": proposal.status},
                request_id=str(request.request_id),
            )
        prior = None
        if proposal.effective_semantic_review_id is not None:
            prior = self._governance.get_semantic_review(proposal.effective_semantic_review_id)
        if prior is None:
            raise ValidationFailedError(
                "No semantic review available to challenge",
                details={"proposal_id": str(proposal.id)},
                request_id=str(request.request_id),
            )
        if not prior.challengeable or prior.decision in {
            SemanticDecision.APPROVE.value,
        }:
            raise ValidationFailedError(
                "Effective semantic review is not challengeable",
                details={"review_id": str(prior.id), "decision": prior.decision},
                request_id=str(request.request_id),
            )
        if not _challenge_is_substantive(request.challenge_reason):
            challenge = self._governance.create_semantic_challenge(
                proposal_id=proposal.id,
                against_review_id=prior.id,
                challenge_reason=request.challenge_reason,
                evidence_refs=request.evidence_refs,
                proposed_revision=request.proposed_revision,
                created_by_actor_id=actor_id,
                status=SemanticChallengeStatus.REJECTED_AS_INSUBSTANTIVE,
            )
            raise ValidationFailedError(
                "Challenge must include substantive new rationale or evidence",
                details={
                    "challenge_id": str(challenge.id),
                    "reason": "challenge_insubstantive",
                },
                request_id=str(request.request_id),
            )

        challenge = self._governance.create_semantic_challenge(
            proposal_id=proposal.id,
            against_review_id=prior.id,
            challenge_reason=request.challenge_reason,
            evidence_refs=request.evidence_refs,
            proposed_revision=request.proposed_revision,
            created_by_actor_id=actor_id,
            status=SemanticChallengeStatus.ACCEPTED_FOR_REVIEW,
        )
        proposal_type = ProposalType(proposal.proposal_type)
        review_context = SemanticReviewContextBuilder(self._session, self._settings).build(
            proposal_type=proposal_type,
            payload=proposal.payload,
        )
        prior_structured = _review_row_to_structured(prior)
        try:
            raw = self._reviewer.review(
                ReviewRequest(
                    proposal_type=proposal_type,
                    payload=proposal.payload,
                    actor_id=actor_id,
                    request_id=request.request_id,
                    context=review_context,
                    prior_decision=prior_structured,
                    challenge_reason=request.challenge_reason,
                    evidence_refs=request.evidence_refs,
                    proposed_revision=request.proposed_revision,
                    review_stage="challenge",
                )
            )
        except Exception as exc:  # noqa: BLE001
            structured = StructuredReviewResult(
                decision=SemanticDecision.MANUAL_REVIEW,
                confidence=0.0,
                summary="Semantic reviewer unavailable during challenge",
                reasons=[
                    ReviewReason(
                        code="semantic_reviewer_unavailable",
                        message="Semantic reviewer call failed",
                    )
                ],
                context_sufficient=False,
                challengeable=True,
                previous_decision=SemanticDecision(prior.decision),
                decision_changed=False,
            )
            details = {"reason": "reviewer_failure", "error_type": type(exc).__name__}
            input_tokens = output_tokens = None
            prompt_version = PROMPT_TEMPLATE_VERSION
        else:
            structured = raw.structured or StructuredReviewResult(
                decision=SemanticDecision(raw.decision.value),
                confidence=0.0,
                summary=raw.reason,
                reasons=[ReviewReason(code="unstructured", message=raw.reason)],
                context_sufficient=True,
                challengeable=True,
            )
            proposal_key = str(proposal.payload.get("key") or "")
            structured = apply_confidence_policy(
                structured,
                settings=self._settings,
                challenge=True,
                proposal_key=proposal_key,
                proposal_description=str(proposal.payload.get("description") or ""),
            )
            structured = structured.model_copy(
                update={
                    "previous_decision": SemanticDecision(prior.decision),
                    "decision_changed": structured.decision.value != prior.decision
                    and structured.decision != SemanticDecision.UPHOLD_REJECTION,
                }
            )
            if (
                structured.decision == SemanticDecision.UPHOLD_REJECTION
                or structured.decision.value == prior.decision
            ):
                structured = structured.model_copy(update={"decision_changed": False})
            if (
                structured.decision == SemanticDecision.APPROVE
                and prior.decision != SemanticDecision.APPROVE.value
            ):
                structured = structured.model_copy(update={"decision_changed": True})
            details = dict(raw.details)
            input_tokens = raw.input_tokens
            output_tokens = raw.output_tokens
            prompt_version = raw.prompt_template_version

        shadow = self._settings.semantic_review_mode == "shadow"
        review = self._governance.create_semantic_review(
            proposal_id=proposal.id,
            review_stage=SemanticReviewStage.CHALLENGE,
            previous_review_id=prior.id,
            challenge_id=challenge.id,
            provider=str(getattr(self._reviewer, "_provider", "unknown")),
            model=str(getattr(self._reviewer, "_model", "unknown")),
            model_version=self._settings.semantic_review_model_version,
            prompt_template_version=prompt_version,
            context_builder_version=review_context.builder_version,
            input_hash=review_context.input_hash,
            context_concept_keys=review_context.concept_keys(),
            decision=structured.decision.value,
            confidence=structured.confidence,
            summary=structured.summary,
            reasons=[item.model_dump(mode="json") for item in structured.reasons],
            related_existing_concepts=[
                item.model_dump(mode="json") for item in structured.related_existing_concepts
            ],
            recommended_actions=list(structured.recommended_actions),
            context_sufficient=structured.context_sufficient,
            challengeable=structured.challengeable
            and structured.decision != SemanticDecision.APPROVE,
            authoritative=not shadow,
            previous_decision=prior.decision,
            decision_changed=structured.decision_changed,
            llm_call_log_id=_optional_uuid(details.get("llm_call_log_id")),
            details={
                **details,
                "shadow": shadow,
                "model_decision": structured.decision.value,
                "authoritative": not shadow,
                "candidate_selection_trace": list(review_context.candidate_selection_trace),
                "required_clarification": list(structured.required_clarification),
            },
        )
        self._governance.set_challenge_status(
            challenge, status=SemanticChallengeStatus.REVIEWED
        )
        self._governance.set_effective_semantic_review(proposal, review.id)
        self._maybe_issue_clarification_request(proposal=proposal, review=review)
        # Do NOT mutate historical ontology_gate_result rows.
        from semantic_memory.services.gates import GateOutcome

        outcome, status, reason = self._aggregate(
            [
                GateOutcome(
                    gate_name=item.gate_name,
                    decision=GateDecision(item.decision),
                    details=item.details or {},
                )
                for item in self._governance.list_gate_results(proposal.id)
            ],
            proposal=proposal,
        )
        self._governance.set_proposal_status(proposal, status=status, decision_reason=reason)
        SEMANTIC_REVIEW_METRICS.record_review(
            decision=structured.decision.value,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            challenge=True,
            overturn=bool(structured.decision_changed),
        )
        return ChallengeOntologyReviewResponse(
            outcome=outcome.value,
            proposal_id=proposal.id,
            challenge_id=challenge.id,
            review=self._to_semantic_review_response(review),
            open_clarification_request=self._open_clarification_response(proposal.id),
            request_id=request.request_id,
        )

    def answer_semantic_clarification(
        self, request: AnswerSemanticClarificationRequest
    ) -> AnswerSemanticClarificationResponse:
        actor = self._actors.require_active_actor(request.actor_key)
        self._actors.require_capability(actor, Capability.ONTOLOGY_PROPOSE)
        return self._mutations.run(
            actor=actor,
            operation_name="answer_semantic_clarification",
            request=request,
            response_model=AnswerSemanticClarificationResponse,
            constraint_name="ontology_propose",
            execute=lambda: self._answer_clarification_body(
                request=request, actor_id=actor.id
            ),
        )

    def _answer_clarification_body(
        self, *, request: AnswerSemanticClarificationRequest, actor_id: uuid.UUID
    ) -> AnswerSemanticClarificationResponse:
        clarification = self._governance.get_clarification_request(
            request.clarification_request_id
        )
        if clarification is None:
            raise ClarificationRequestNotFoundError(
                "Clarification request was not found",
                details={
                    "clarification_request_id": str(request.clarification_request_id)
                },
                request_id=str(request.request_id),
            )
        if clarification.status == SemanticClarificationStatus.SUPERSEDED.value:
            raise ClarificationRequestSupersededError(
                "Clarification request was superseded by a newer review",
                details={
                    "clarification_request_id": str(clarification.id),
                    "status": clarification.status,
                },
                request_id=str(request.request_id),
            )
        if clarification.status in {
            SemanticClarificationStatus.ANSWERED.value,
            SemanticClarificationStatus.RESOLVED.value,
        }:
            raise ClarificationRequestAlreadyResolvedError(
                "Clarification request was already answered",
                details={
                    "clarification_request_id": str(clarification.id),
                    "status": clarification.status,
                },
                request_id=str(request.request_id),
            )
        if clarification.status != SemanticClarificationStatus.OPEN.value:
            raise ClarificationRequestAlreadyResolvedError(
                "Clarification request is not open",
                details={
                    "clarification_request_id": str(clarification.id),
                    "status": clarification.status,
                },
                request_id=str(request.request_id),
            )

        proposal = self._governance.get_proposal(clarification.proposal_id)
        if proposal is None:
            raise UnknownProposalError(
                f"Proposal {clarification.proposal_id} was not found",
                details={"proposal_id": str(clarification.proposal_id)},
                request_id=str(request.request_id),
            )
        prior = self._governance.get_semantic_review(clarification.review_id)
        if prior is None:
            raise ValidationFailedError(
                "Semantic review for clarification request is missing",
                details={"review_id": str(clarification.review_id)},
                request_id=str(request.request_id),
            )

        answer = request.response.strip()
        if len(answer) < 20:
            raise ValidationFailedError(
                "Clarification response must include substantive semantic content",
                details={"reason": "clarification_insubstantive"},
                request_id=str(request.request_id),
            )

        self._governance.mark_clarification_answered(
            clarification,
            answer_text=answer,
            answered_by_actor_id=actor_id,
            evidence_refs=request.evidence_refs,
        )

        proposal_type = ProposalType(proposal.proposal_type)
        review_context = SemanticReviewContextBuilder(self._session, self._settings).build(
            proposal_type=proposal_type,
            payload=proposal.payload,
        )
        prior_structured = _review_row_to_structured(prior)
        proposal_key = str(proposal.payload.get("key") or "")
        try:
            raw = self._reviewer.review(
                ReviewRequest(
                    proposal_type=proposal_type,
                    payload=proposal.payload,
                    actor_id=actor_id,
                    request_id=request.request_id,
                    context=review_context,
                    prior_decision=prior_structured,
                    challenge_reason=answer,
                    evidence_refs=request.evidence_refs,
                    proposed_revision=request.proposed_revision,
                    review_stage="clarification",
                )
            )
        except Exception as exc:  # noqa: BLE001
            structured = StructuredReviewResult(
                decision=SemanticDecision.MANUAL_REVIEW,
                confidence=0.0,
                summary="Semantic reviewer unavailable during clarification",
                reasons=[
                    ReviewReason(
                        code="semantic_reviewer_unavailable",
                        message="Semantic reviewer call failed",
                    )
                ],
                context_sufficient=False,
                challengeable=True,
                previous_decision=SemanticDecision(prior.decision),
                decision_changed=False,
            )
            details = {"reason": "reviewer_failure", "error_type": type(exc).__name__}
            input_tokens = output_tokens = None
            prompt_version = PROMPT_TEMPLATE_VERSION
        else:
            structured = raw.structured or StructuredReviewResult(
                decision=SemanticDecision(raw.decision.value),
                confidence=0.0,
                summary=raw.reason,
                reasons=[ReviewReason(code="unstructured", message=raw.reason)],
                context_sufficient=True,
                challengeable=True,
            )
            structured = apply_confidence_policy(
                structured,
                settings=self._settings,
                challenge=True,
                proposal_key=proposal_key,
                proposal_description=str(proposal.payload.get("description") or ""),
            )
            structured = structured.model_copy(
                update={
                    "previous_decision": SemanticDecision(prior.decision),
                    "decision_changed": structured.decision.value != prior.decision,
                }
            )
            details = dict(raw.details)
            input_tokens = raw.input_tokens
            output_tokens = raw.output_tokens
            prompt_version = raw.prompt_template_version

        shadow = self._settings.semantic_review_mode == "shadow"
        review = self._governance.create_semantic_review(
            proposal_id=proposal.id,
            review_stage=SemanticReviewStage.CLARIFICATION,
            previous_review_id=prior.id,
            provider=str(getattr(self._reviewer, "_provider", "unknown")),
            model=str(getattr(self._reviewer, "_model", "unknown")),
            model_version=self._settings.semantic_review_model_version,
            prompt_template_version=prompt_version,
            context_builder_version=review_context.builder_version,
            input_hash=review_context.input_hash,
            context_concept_keys=review_context.concept_keys(),
            decision=structured.decision.value,
            confidence=structured.confidence,
            summary=structured.summary,
            reasons=[item.model_dump(mode="json") for item in structured.reasons],
            related_existing_concepts=[
                item.model_dump(mode="json") for item in structured.related_existing_concepts
            ],
            recommended_actions=list(structured.recommended_actions),
            context_sufficient=structured.context_sufficient,
            challengeable=structured.challengeable
            and structured.decision != SemanticDecision.APPROVE,
            authoritative=not shadow,
            previous_decision=prior.decision,
            decision_changed=structured.decision_changed,
            llm_call_log_id=_optional_uuid(details.get("llm_call_log_id")),
            details={
                **details,
                "shadow": shadow,
                "model_decision": structured.decision.value,
                "authoritative": not shadow,
                "candidate_selection_trace": list(review_context.candidate_selection_trace),
                "required_clarification": list(structured.required_clarification),
                "answered_clarification_request_id": str(clarification.id),
            },
        )
        self._governance.mark_clarification_resolved(
            clarification, resulting_review_id=review.id
        )
        self._governance.set_effective_semantic_review(proposal, review.id)
        self._maybe_issue_clarification_request(proposal=proposal, review=review)

        from semantic_memory.services.gates import GateOutcome

        outcome, status, reason = self._aggregate(
            [
                GateOutcome(
                    gate_name=item.gate_name,
                    decision=GateDecision(item.decision),
                    details=item.details or {},
                )
                for item in self._governance.list_gate_results(proposal.id)
            ],
            proposal=proposal,
        )
        self._governance.set_proposal_status(proposal, status=status, decision_reason=reason)
        SEMANTIC_REVIEW_METRICS.record_review(
            decision=structured.decision.value,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            challenge=True,
            overturn=bool(structured.decision_changed),
        )
        # Count clarification rounds as clarification-stage reviews on this proposal.
        clar_rounds = sum(
            1
            for item in self._governance.list_semantic_reviews(proposal.id)
            if item.review_stage == SemanticReviewStage.CLARIFICATION.value
        )
        SEMANTIC_REVIEW_METRICS.record_clarification_resolved(
            rounds_for_proposal=max(1, clar_rounds)
        )
        # Refresh status after resolve (+ optional new open ask).
        self._session.refresh(clarification)
        return AnswerSemanticClarificationResponse(
            outcome=outcome.value,
            proposal_id=proposal.id,
            clarification_request_id=clarification.id,
            clarification_status=SemanticClarificationStatus(clarification.status),
            resolved_by_review_id=clarification.resulting_review_id,
            review=self._to_semantic_review_response(review),
            open_clarification_request=self._open_clarification_response(proposal.id),
            request_id=request.request_id,
        )

    def _maybe_issue_clarification_request(
        self,
        *,
        proposal: OntologyProposal,
        review: OntologySemanticReview,
    ) -> OntologySemanticClarificationRequest | None:
        """Create a deterministic clarification_request_id when review needs more semantics."""
        if review.decision != SemanticDecision.MANUAL_REVIEW.value:
            return None

        details = dict(review.details or {})
        asks = [str(x) for x in details.get("required_clarification") or [] if str(x).strip()]
        related = list(review.related_existing_concepts or [])
        reason_codes = {
            str(item.get("code"))
            for item in (review.reasons or [])
            if isinstance(item, dict) and item.get("code")
        }
        ambiguity_codes = {
            CLARIFICATION_REASON_CODE,
            "ambiguous_semantic_distinction",
            "semantic_distinction_unclear",
        }
        if not asks and not related and not (reason_codes & ambiguity_codes):
            return None

        if not asks and related:
            proposal_key = str(proposal.payload.get("key") or "the proposal")
            asks = default_clarification_asks(
                proposal_key=proposal_key,
                related=[RelatedExistingConcept.model_validate(item) for item in related],
            )

        if not asks:
            asks = ["Please explain the intended semantic distinction from related concepts."]

        superseded_ids = self._governance.supersede_open_clarifications(proposal.id)
        clarification = self._governance.create_clarification_request(
            proposal_id=proposal.id,
            review_id=review.id,
            reason_code=CLARIFICATION_REASON_CODE,
            question=asks[0],
            required_clarification=asks,
            related_existing_concepts=related,
            supersedes_clarification_request_id=(
                superseded_ids[0] if superseded_ids else None
            ),
        )
        review.details = {
            **details,
            "clarification_request_id": str(clarification.id),
            "reason_code": CLARIFICATION_REASON_CODE,
            "required_clarification": asks,
            "supersedes_clarification_request_id": (
                str(clarification.supersedes_clarification_request_id)
                if clarification.supersedes_clarification_request_id
                else None
            ),
        }
        self._session.flush()
        SEMANTIC_REVIEW_METRICS.record_clarification_opened()
        return clarification

    def _open_clarification_response(
        self, proposal_id: uuid.UUID
    ) -> ClarificationRequestResponse | None:
        row = self._governance.get_open_clarification_for_proposal(proposal_id)
        if row is None:
            return None
        return self._to_clarification_response(row)

    def _to_clarification_response(
        self, row: OntologySemanticClarificationRequest
    ) -> ClarificationRequestResponse:
        return ClarificationRequestResponse(
            clarification_request_id=row.id,
            proposal_id=row.proposal_id,
            review_id=row.review_id,
            reason_code=row.reason_code,
            question=row.question,
            required_clarification=[str(x) for x in row.required_clarification or []],
            related_existing_concepts=[
                RelatedExistingConcept.model_validate(item)
                for item in row.related_existing_concepts or []
            ],
            clarification_status=SemanticClarificationStatus(row.status),
            supersedes_clarification_request_id=row.supersedes_clarification_request_id,
            resolved_by_review_id=row.resulting_review_id,
            created_at=row.created_at,
        )

    def _apply_body(
        self, *, request: ApplyProposalRequest, actor_id: uuid.UUID
    ) -> ApplyProposalResponse:
        proposal = self._governance.get_proposal(request.proposal_id)
        if proposal is None:
            raise UnknownProposalError(
                f"Proposal {request.proposal_id} was not found",
                details={"proposal_id": str(request.proposal_id)},
                request_id=str(request.request_id),
            )
        if proposal.status not in {
            ProposalStatus.SUBMITTED.value,
            ProposalStatus.IN_REVIEW.value,
        }:
            raise ValidationFailedError(
                f"Proposal {proposal.id} is not applyable",
                details={"status": proposal.status},
                request_id=str(request.request_id),
            )
        gates = self._governance.list_gate_results(proposal.id)
        effective_semantic = self._effective_semantic_gate_decision(proposal)
        blocking = []
        for item in gates:
            if item.gate_name == "semantic_review":
                decision = effective_semantic or GateDecision(item.decision)
            else:
                decision = GateDecision(item.decision)
            if decision == GateDecision.FAIL:
                blocking.append(item.gate_name)
            if decision == GateDecision.REUSE_RECOMMENDED:
                raise ValidationFailedError(
                    "Cannot apply a proposal that recommends reuse",
                    details={"proposal_id": str(proposal.id)},
                    request_id=str(request.request_id),
                )
        if blocking:
            raise ValidationFailedError(
                "Cannot apply a proposal with failed gates",
                details={"proposal_id": str(proposal.id), "failed_gates": blocking},
                request_id=str(request.request_id),
            )
        proposal_type = ProposalType(proposal.proposal_type)
        self._revalidate_before_apply(
            proposal_id=proposal.id,
            proposal_type=proposal_type,
            payload=proposal.payload,
            request_id=request.request_id,
        )
        self._apply_payload(
            proposal=proposal,
            proposal_type=proposal_type,
            actor_id=actor_id,
            request_id=request.request_id,
        )
        self._governance.set_proposal_status(
            proposal,
            status=ProposalStatus.ACCEPTED,
            decision_reason="Applied by authorized actor",
        )
        return ApplyProposalResponse(
            outcome=ProposalOutcome.APPLIED,
            proposal=self._to_proposal_response(proposal),
            request_id=request.request_id,
        )

    def _revalidate_before_apply(
        self,
        *,
        proposal_id: uuid.UUID,
        proposal_type: ProposalType,
        payload: dict[str, Any],
        request_id: uuid.UUID,
    ) -> None:
        """Re-check deterministic gates against the live ontology before mutate."""
        outcomes = self._gates.revalidate_apply(proposal_type=proposal_type, payload=payload)
        details = {
            "proposal_id": str(proposal_id),
            "gate_results": [
                {"gate_name": item.gate_name, "decision": item.decision.value, **item.details}
                for item in outcomes
            ],
        }
        if any(item.decision == GateDecision.FAIL for item in outcomes):
            if any(
                item.gate_name == "cycle" and item.decision == GateDecision.FAIL
                for item in outcomes
            ):
                raise OntologyCycleError(
                    "Ontology apply rejected: inheritance cycle",
                    details=details,
                    request_id=str(request_id),
                )
            raise OntologyProposalRejectedError(
                "Ontology apply rejected by deterministic revalidation",
                details=details,
                request_id=str(request_id),
            )
        if any(item.decision == GateDecision.REUSE_RECOMMENDED for item in outcomes):
            raise OntologyReuseRecommendedError(
                "Ontology apply rejected: reuse existing concept",
                details=details,
                request_id=str(request_id),
            )

    def _require_class(
        self, *, namespace_key: str, class_key: str, request_id: uuid.UUID
    ) -> OntologyClass:
        row = self._ontology.get_class_by_key(namespace_key=namespace_key, class_key=class_key)
        if row is None:
            raise UnknownClassError(
                f"Unknown class '{namespace_key}:{class_key}'",
                details={"namespace_key": namespace_key, "class_key": class_key},
                request_id=str(request_id),
            )
        return row

    def _require_predicate(
        self, *, namespace_key: str, predicate_key: str, request_id: uuid.UUID
    ) -> OntologyPredicate:
        row = self._ontology.get_predicate_by_key(
            namespace_key=namespace_key, predicate_key=predicate_key
        )
        if row is None:
            raise UnknownPredicateError(
                f"Unknown predicate '{namespace_key}:{predicate_key}'",
                details={"namespace_key": namespace_key, "predicate_key": predicate_key},
                request_id=str(request_id),
            )
        return row

    def _apply_payload(
        self,
        *,
        proposal: OntologyProposal,
        proposal_type: ProposalType,
        actor_id: uuid.UUID,
        request_id: uuid.UUID,
    ) -> None:
        payload = proposal.payload
        namespace_key = str(payload["namespace_key"])
        namespace = self._session.scalar(
            select(OntologyNamespace).where(OntologyNamespace.key == namespace_key)
        )
        if namespace is None:
            raise ValidationFailedError(
                f"Unknown namespace '{namespace_key}'",
                details={"namespace_key": namespace_key},
                request_id=str(request_id),
            )

        if proposal_type == ProposalType.CLASS:
            self._apply_class(proposal, namespace, actor_id, request_id)
        elif proposal_type == ProposalType.PREDICATE:
            self._apply_predicate(proposal, namespace, actor_id, request_id)
        elif proposal_type == ProposalType.CONSTRAINT:
            self._apply_constraint(proposal, namespace, actor_id)
        elif proposal_type == ProposalType.ALIAS:
            self._apply_alias(proposal, namespace, actor_id, request_id)
        elif proposal_type == ProposalType.CLASS_PARENT:
            self._apply_class_parent(proposal, namespace, actor_id, request_id)
        else:
            raise ValidationFailedError(
                f"Unsupported proposal type '{proposal_type}'",
                request_id=str(request_id),
            )

    def _apply_class(
        self,
        proposal: OntologyProposal,
        namespace: OntologyNamespace,
        actor_id: uuid.UUID,
        request_id: uuid.UUID,
    ) -> None:
        payload = proposal.payload
        ontology_class = OntologyClass(
            id=uuid.uuid4(),
            namespace_id=namespace.id,
            key=str(payload["key"]),
        )
        self._session.add(ontology_class)
        self._session.flush()
        revision = OntologyClassRevision(
            id=uuid.uuid4(),
            class_id=ontology_class.id,
            revision_number=1,
            label=str(payload.get("label") or payload["key"]),
            description=payload.get("description"),
            metadata_json=payload.get("metadata") or {},
            created_by_actor_id=actor_id,
        )
        self._session.add(revision)
        self._session.flush()
        ontology_class.current_revision_id = revision.id
        for parent_key in payload.get("parent_keys") or []:
            parent = self._require_class(
                namespace_key=namespace.key,
                class_key=str(parent_key),
                request_id=request_id,
            )
            self._session.add(
                OntologyClassParent(
                    id=uuid.uuid4(),
                    child_class_id=ontology_class.id,
                    parent_class_id=parent.id,
                )
            )
        self._session.flush()
        self._governance.create_change(
            proposal_id=proposal.id,
            object_type=OntologyChangeObjectType.CLASS,
            object_id=ontology_class.id,
            previous_revision_id=None,
            new_revision_id=revision.id,
            change_summary=f"Created class {namespace.key}:{ontology_class.key}",
            applied_by_actor_id=actor_id,
        )

    def _apply_predicate(
        self,
        proposal: OntologyProposal,
        namespace: OntologyNamespace,
        actor_id: uuid.UUID,
        request_id: uuid.UUID,
    ) -> None:
        payload = proposal.payload
        existing = self._ontology.get_predicate_by_key(
            namespace_key=namespace.key, predicate_key=str(payload["key"])
        )
        if existing is not None:
            current = self._ontology.get_current_predicate_revision(existing)
            current_number = 0 if current is None else current.revision_number
            base = proposal.base_revision_number
            if base is None or base != current_number:
                raise RevisionConflictError(
                    "Predicate revision is stale",
                    details={
                        "expected_base_revision": base,
                        "current_revision": current_number,
                        "predicate_key": payload["key"],
                    },
                    request_id=str(request_id),
                )
            previous_revision_id = None if current is None else current.id
            next_number = current_number + 1
            predicate = existing
        else:
            if proposal.base_revision_number not in (None, 0):
                raise RevisionConflictError(
                    "Cannot apply base revision for a new predicate",
                    details={"base_revision_number": proposal.base_revision_number},
                    request_id=str(request_id),
                )
            predicate = OntologyPredicate(
                id=uuid.uuid4(),
                namespace_id=namespace.id,
                key=str(payload["key"]),
            )
            self._session.add(predicate)
            self._session.flush()
            previous_revision_id = None
            next_number = 1

        revision = OntologyPredicateRevision(
            id=uuid.uuid4(),
            predicate_id=predicate.id,
            revision_number=next_number,
            label=str(payload.get("label") or payload["key"]),
            description=payload.get("description"),
            value_kind=str(payload["value_kind"]),
            datatype=payload.get("datatype"),
            cardinality=str(payload.get("cardinality") or Cardinality.MANY.value),
            is_symmetric=bool(payload.get("is_symmetric", False)),
            is_transitive=bool(payload.get("is_transitive", False)),
            metadata_json=payload.get("metadata") or {},
            created_by_actor_id=actor_id,
        )
        self._session.add(revision)
        self._session.flush()
        predicate.current_revision_id = revision.id
        for domain_key in payload.get("domain_keys") or []:
            domain = self._require_class(
                namespace_key=namespace.key,
                class_key=str(domain_key),
                request_id=request_id,
            )
            self._session.add(
                OntologyPredicateDomain(
                    id=uuid.uuid4(),
                    predicate_revision_id=revision.id,
                    class_id=domain.id,
                )
            )
        for range_key in payload.get("range_keys") or []:
            range_class = self._require_class(
                namespace_key=namespace.key,
                class_key=str(range_key),
                request_id=request_id,
            )
            self._session.add(
                OntologyPredicateRange(
                    id=uuid.uuid4(),
                    predicate_revision_id=revision.id,
                    class_id=range_class.id,
                )
            )
        self._session.flush()
        self._governance.create_change(
            proposal_id=proposal.id,
            object_type=OntologyChangeObjectType.PREDICATE,
            object_id=predicate.id,
            previous_revision_id=previous_revision_id,
            new_revision_id=revision.id,
            change_summary=f"Applied predicate {namespace.key}:{predicate.key} r{next_number}",
            applied_by_actor_id=actor_id,
        )

    def _apply_constraint(
        self,
        proposal: OntologyProposal,
        namespace: OntologyNamespace,
        actor_id: uuid.UUID,
    ) -> None:
        payload = proposal.payload
        constraint = OntologyConstraint(
            id=uuid.uuid4(),
            namespace_id=namespace.id,
            key=str(payload["key"]),
            constraint_type=str(payload["constraint_type"]),
            expression=payload.get("expression") or {},
            description=payload.get("description"),
        )
        self._session.add(constraint)
        self._session.flush()
        self._governance.create_change(
            proposal_id=proposal.id,
            object_type=OntologyChangeObjectType.CONSTRAINT,
            object_id=constraint.id,
            change_summary=f"Created constraint {namespace.key}:{constraint.key}",
            applied_by_actor_id=actor_id,
        )

    def _apply_alias(
        self,
        proposal: OntologyProposal,
        namespace: OntologyNamespace,
        actor_id: uuid.UUID,
        request_id: uuid.UUID,
    ) -> None:
        payload = proposal.payload
        target_type = AliasTargetType(str(payload["target_type"]))
        class_id = None
        predicate_id = None
        if target_type == AliasTargetType.CLASS:
            target = self._require_class(
                namespace_key=namespace.key,
                class_key=str(payload["target_key"]),
                request_id=request_id,
            )
            class_id = target.id
        else:
            target_p = self._require_predicate(
                namespace_key=namespace.key,
                predicate_key=str(payload["target_key"]),
                request_id=request_id,
            )
            predicate_id = target_p.id
        alias = OntologyAlias(
            id=uuid.uuid4(),
            namespace_id=namespace.id,
            alias=str(payload["alias"]),
            target_type=target_type.value,
            class_id=class_id,
            predicate_id=predicate_id,
        )
        self._session.add(alias)
        self._session.flush()
        self._governance.create_change(
            proposal_id=proposal.id,
            object_type=OntologyChangeObjectType.ALIAS,
            object_id=alias.id,
            change_summary=f"Created alias {payload['alias']}",
            applied_by_actor_id=actor_id,
        )

    def _apply_class_parent(
        self,
        proposal: OntologyProposal,
        namespace: OntologyNamespace,
        actor_id: uuid.UUID,
        request_id: uuid.UUID,
    ) -> None:
        payload = proposal.payload
        child = self._require_class(
            namespace_key=namespace.key,
            class_key=str(payload["child_key"]),
            request_id=request_id,
        )
        parent = self._require_class(
            namespace_key=namespace.key,
            class_key=str(payload["parent_key"]),
            request_id=request_id,
        )
        revision = self._ontology.get_current_class_revision(child)
        current_number = 0 if revision is None else revision.revision_number
        if (
            proposal.base_revision_number is not None
            and proposal.base_revision_number != current_number
        ):
            raise RevisionConflictError(
                "Class revision is stale for parent link",
                details={
                    "expected_base_revision": proposal.base_revision_number,
                    "current_revision": current_number,
                    "child_key": payload["child_key"],
                },
                request_id=str(request_id),
            )
        link = OntologyClassParent(
            id=uuid.uuid4(),
            child_class_id=child.id,
            parent_class_id=parent.id,
        )
        self._session.add(link)
        self._session.flush()
        self._governance.create_change(
            proposal_id=proposal.id,
            object_type=OntologyChangeObjectType.CLASS_PARENT,
            object_id=link.id,
            change_summary=(f"Linked {payload['child_key']} -> {payload['parent_key']}"),
            applied_by_actor_id=actor_id,
        )

    def _to_proposal_response(self, proposal: OntologyProposal) -> ProposalResponse:
        gates = [
            GateResultResponse(
                gate_name=item.gate_name,
                decision=GateDecision(item.decision),
                details=item.details or {},
                created_at=item.created_at,
            )
            for item in self._governance.list_gate_results(proposal.id)
        ]
        changes = [
            OntologyChangeResponse(
                id=item.id,
                object_type=item.object_type,
                object_id=item.object_id,
                previous_revision_id=item.previous_revision_id,
                new_revision_id=item.new_revision_id,
                change_summary=item.change_summary,
                applied_by_actor_id=item.applied_by_actor_id,
                created_at=item.created_at,
            )
            for item in self._governance.list_changes(proposal.id)
        ]
        reviews = [
            self._to_semantic_review_response(item)
            for item in self._governance.list_semantic_reviews(proposal.id)
        ]
        effective = None
        if proposal.effective_semantic_review_id is not None:
            effective = next(
                (item for item in reviews if item.id == proposal.effective_semantic_review_id),
                None,
            )
            if effective is None:
                row = self._governance.get_semantic_review(proposal.effective_semantic_review_id)
                if row is not None:
                    effective = self._to_semantic_review_response(row)
        return ProposalResponse(
            id=proposal.id,
            proposal_type=ProposalType(proposal.proposal_type),
            status=ProposalStatus(proposal.status),
            summary=proposal.summary,
            payload=proposal.payload,
            base_revision_number=proposal.base_revision_number,
            request_id=proposal.request_id,
            decision_reason=proposal.decision_reason,
            gate_results=gates,
            changes=changes,
            effective_semantic_review_id=proposal.effective_semantic_review_id,
            effective_semantic_review=effective,
            semantic_reviews=reviews,
            created_at=proposal.created_at,
            updated_at=proposal.updated_at,
        )

    def _to_semantic_review_response(
        self, row: OntologySemanticReview
    ) -> SemanticReviewResponse:
        details = row.details or {}
        clarification_id = _optional_uuid(details.get("clarification_request_id"))
        if clarification_id is None:
            linked = self._governance.get_clarification_for_review(row.id)
            if linked is not None:
                clarification_id = linked.id
        return SemanticReviewResponse(
            id=row.id,
            proposal_id=row.proposal_id,
            review_stage=SemanticReviewStage(row.review_stage),
            previous_review_id=row.previous_review_id,
            challenge_id=row.challenge_id,
            clarification_request_id=clarification_id,
            provider=row.provider,
            model=row.model,
            model_version=row.model_version,
            prompt_template_version=row.prompt_template_version,
            context_builder_version=row.context_builder_version,
            input_hash=row.input_hash,
            context_concept_keys=[str(x) for x in row.context_concept_keys or []],
            candidate_selection_trace=list(details.get("candidate_selection_trace") or []),
            decision=SemanticDecision(row.decision),
            confidence=None if row.confidence is None else float(row.confidence),
            summary=row.summary,
            reasons=[ReviewReason.model_validate(item) for item in row.reasons or []],
            related_existing_concepts=[
                RelatedExistingConcept.model_validate(item)
                for item in row.related_existing_concepts or []
            ],
            recommended_actions=[str(x) for x in row.recommended_actions or []],
            required_clarification=[
                str(x) for x in details.get("required_clarification") or []
            ],
            context_sufficient=bool(row.context_sufficient),
            challengeable=bool(row.challengeable),
            authoritative=bool(row.authoritative),
            previous_decision=(
                None
                if row.previous_decision is None
                else SemanticDecision(row.previous_decision)
            ),
            decision_changed=row.decision_changed,
            llm_call_log_id=row.llm_call_log_id,
            details=details,
            created_at=row.created_at,
        )


def _optional_uuid(value: Any) -> uuid.UUID | None:
    if value is None or value == "":
        return None
    return uuid.UUID(str(value))


def _challenge_is_substantive(reason: str) -> bool:
    cleaned = " ".join(reason.strip().split())
    if len(cleaned) < 40:
        return False
    lowered = cleaned.casefold()
    trivial = {
        "please reconsider",
        "please reconsider.",
        "reconsider",
        "try again",
        "wrong",
        "i disagree",
    }
    return lowered not in trivial


def _review_row_to_structured(row: OntologySemanticReview) -> StructuredReviewResult:
    details = row.details or {}
    return StructuredReviewResult(
        decision=SemanticDecision(row.decision),
        confidence=0.0 if row.confidence is None else float(row.confidence),
        summary=row.summary,
        reasons=[ReviewReason.model_validate(item) for item in row.reasons or []],
        related_existing_concepts=[
            RelatedExistingConcept.model_validate(item)
            for item in row.related_existing_concepts or []
        ],
        recommended_actions=[str(x) for x in row.recommended_actions or []],
        required_clarification=[
            str(x) for x in details.get("required_clarification") or []
        ],
        context_sufficient=bool(row.context_sufficient),
        challengeable=bool(row.challengeable),
    )
