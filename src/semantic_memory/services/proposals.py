"""Governed ontology proposal and apply services."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from semantic_memory.exceptions import (
    RevisionConflictError,
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
    ProposalStatus,
)
from semantic_memory.models.capabilities import Capability
from semantic_memory.models.enums import GateDecision, ProposalType
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
from semantic_memory.services.actors import ActorService
from semantic_memory.services.gates import DeterministicGatePipeline
from semantic_memory.services.mutations import MutationRunner


class ProposalService:
    def __init__(self, session: Session) -> None:
        self._session = session
        self._actors = ActorService(session)
        self._governance = GovernanceRepository(session)
        self._ontology = OntologyRepository(session)
        self._gates = DeterministicGatePipeline(session)
        self._mutations = MutationRunner(session)

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
        outcomes = self._gates.run(
            proposal_id=proposal.id,
            proposal_type=proposal_type,
            payload=payload,
        )
        outcome, status, reason = self._aggregate(outcomes)
        self._governance.set_proposal_status(proposal, status=status, decision_reason=reason)
        return ProposeResponse(
            outcome=outcome,
            proposal=self._to_proposal_response(proposal),
            request_id=request_id,
        )

    def _aggregate(self, outcomes: list[Any]) -> tuple[ProposalOutcome, ProposalStatus, str | None]:
        if any(item.decision == GateDecision.FAIL for item in outcomes):
            failed = [item.gate_name for item in outcomes if item.decision == GateDecision.FAIL]
            return (
                ProposalOutcome.REJECTED,
                ProposalStatus.REJECTED,
                f"Failed gates: {', '.join(failed)}",
            )
        if any(item.decision == GateDecision.REUSE_RECOMMENDED for item in outcomes):
            return (
                ProposalOutcome.REUSE_RECOMMENDED,
                ProposalStatus.REJECTED,
                "Reuse an existing ontology concept",
            )
        if any(item.decision == GateDecision.MANUAL_REVIEW for item in outcomes):
            return (
                ProposalOutcome.MANUAL_REVIEW,
                ProposalStatus.IN_REVIEW,
                "Semantic review required",
            )
        return ProposalOutcome.READY_TO_APPLY, ProposalStatus.SUBMITTED, None

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
        if any(item.decision == GateDecision.FAIL.value for item in gates):
            raise ValidationFailedError(
                "Cannot apply a proposal with failed gates",
                details={"proposal_id": str(proposal.id)},
                request_id=str(request.request_id),
            )
        if any(item.decision == GateDecision.REUSE_RECOMMENDED.value for item in gates):
            raise ValidationFailedError(
                "Cannot apply a proposal that recommends reuse",
                details={"proposal_id": str(proposal.id)},
                request_id=str(request.request_id),
            )
        proposal_type = ProposalType(proposal.proposal_type)
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
            self._apply_class(proposal, namespace, actor_id)
        elif proposal_type == ProposalType.PREDICATE:
            self._apply_predicate(proposal, namespace, actor_id, request_id)
        elif proposal_type == ProposalType.CONSTRAINT:
            self._apply_constraint(proposal, namespace, actor_id)
        elif proposal_type == ProposalType.ALIAS:
            self._apply_alias(proposal, namespace, actor_id)
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
            parent = self._ontology.get_class_by_key(
                namespace_key=namespace.key, class_key=str(parent_key)
            )
            assert parent is not None
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
            domain = self._ontology.get_class_by_key(
                namespace_key=namespace.key, class_key=str(domain_key)
            )
            assert domain is not None
            self._session.add(
                OntologyPredicateDomain(
                    id=uuid.uuid4(),
                    predicate_revision_id=revision.id,
                    class_id=domain.id,
                )
            )
        for range_key in payload.get("range_keys") or []:
            range_class = self._ontology.get_class_by_key(
                namespace_key=namespace.key, class_key=str(range_key)
            )
            assert range_class is not None
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
    ) -> None:
        payload = proposal.payload
        target_type = AliasTargetType(str(payload["target_type"]))
        class_id = None
        predicate_id = None
        if target_type == AliasTargetType.CLASS:
            target = self._ontology.get_class_by_key(
                namespace_key=namespace.key, class_key=str(payload["target_key"])
            )
            assert target is not None
            class_id = target.id
        else:
            target_p = self._ontology.get_predicate_by_key(
                namespace_key=namespace.key, predicate_key=str(payload["target_key"])
            )
            assert target_p is not None
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
        child = self._ontology.get_class_by_key(
            namespace_key=namespace.key, class_key=str(payload["child_key"])
        )
        parent = self._ontology.get_class_by_key(
            namespace_key=namespace.key, class_key=str(payload["parent_key"])
        )
        assert child is not None and parent is not None
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
            created_at=proposal.created_at,
            updated_at=proposal.updated_at,
        )
