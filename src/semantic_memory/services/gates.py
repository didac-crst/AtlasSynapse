"""Deterministic ontology proposal gate pipeline."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.orm import Session

from semantic_memory.models.enums import (
    AliasTargetType,
    GateDecision,
    ProposalType,
    ValueKind,
)
from semantic_memory.repositories.governance import GovernanceRepository
from semantic_memory.repositories.ontology import OntologyRepository


@dataclass
class GateOutcome:
    gate_name: str
    decision: GateDecision
    details: dict[str, Any] = field(default_factory=dict)


class DeterministicGatePipeline:
    """Run schema → … → final_deterministic gates for a proposal payload."""

    def __init__(self, session: Session) -> None:
        self._session = session
        self._ontology = OntologyRepository(session)
        self._governance = GovernanceRepository(session)
        self._extra_gates: list[Callable[[ProposalType, dict[str, Any]], GateOutcome | None]] = []

    def register_extra_gate(
        self, gate: Callable[[ProposalType, dict[str, Any]], GateOutcome | None]
    ) -> None:
        """Hook for Phase 11/12 similarity and semantic-review gates."""
        self._extra_gates.append(gate)

    def run(
        self,
        *,
        proposal_id: Any,
        proposal_type: ProposalType,
        payload: dict[str, Any],
    ) -> list[GateOutcome]:
        outcomes = [
            self._schema(proposal_type, payload),
            self._authorization(),
            self._existing_key(proposal_type, payload),
            self._alias(proposal_type, payload),
            self._structural(proposal_type, payload),
            self._cycle(proposal_type, payload),
            self._domain_range(proposal_type, payload),
        ]
        for extra in self._extra_gates:
            result = extra(proposal_type, payload)
            if result is not None:
                outcomes.append(result)
        outcomes.append(self._final_deterministic(proposal_type, payload, outcomes))

        for outcome in outcomes:
            self._governance.upsert_gate_result(
                proposal_id=proposal_id,
                gate_name=outcome.gate_name,
                decision=outcome.decision,
                details=outcome.details,
            )
        return outcomes

    def _schema(self, proposal_type: ProposalType, payload: dict[str, Any]) -> GateOutcome:
        required: dict[ProposalType, tuple[str, ...]] = {
            ProposalType.CLASS: ("namespace_key", "key"),
            ProposalType.PREDICATE: ("namespace_key", "key", "value_kind", "cardinality"),
            ProposalType.CONSTRAINT: ("namespace_key", "key", "constraint_type"),
            ProposalType.ALIAS: ("namespace_key", "alias", "target_type", "target_key"),
            ProposalType.CLASS_PARENT: ("namespace_key", "child_key", "parent_key"),
        }
        missing = [name for name in required[proposal_type] if not payload.get(name)]
        if missing:
            return GateOutcome(
                "schema",
                GateDecision.FAIL,
                {"missing_fields": missing},
            )
        return GateOutcome("schema", GateDecision.PASS)

    def _authorization(self) -> GateOutcome:
        # Capability checks happen before the pipeline; this records the gate.
        return GateOutcome("authorization", GateDecision.PASS)

    def _existing_key(self, proposal_type: ProposalType, payload: dict[str, Any]) -> GateOutcome:
        namespace_key = str(payload["namespace_key"])
        if proposal_type == ProposalType.CLASS:
            existing = self._ontology.get_class_by_key(
                namespace_key=namespace_key, class_key=str(payload["key"])
            )
            if existing is not None:
                return GateOutcome(
                    "existing_key",
                    GateDecision.REUSE_RECOMMENDED,
                    {
                        "namespace_key": namespace_key,
                        "key": payload["key"],
                        "existing_class_id": str(existing.id),
                    },
                )
        if proposal_type == ProposalType.PREDICATE:
            existing_p = self._ontology.get_predicate_by_key(
                namespace_key=namespace_key, predicate_key=str(payload["key"])
            )
            if existing_p is not None:
                # Updates supply base_revision_number; bare duplicates recommend reuse.
                if payload.get("base_revision_number") is None:
                    return GateOutcome(
                        "existing_key",
                        GateDecision.REUSE_RECOMMENDED,
                        {
                            "namespace_key": namespace_key,
                            "key": payload["key"],
                            "existing_predicate_id": str(existing_p.id),
                        },
                    )
        return GateOutcome("existing_key", GateDecision.PASS)

    def _alias(self, proposal_type: ProposalType, payload: dict[str, Any]) -> GateOutcome:
        if proposal_type != ProposalType.ALIAS:
            return GateOutcome("alias", GateDecision.PASS)
        existing = self._ontology.find_alias(
            namespace_key=str(payload["namespace_key"]),
            alias=str(payload["alias"]),
        )
        if existing is not None:
            return GateOutcome(
                "alias",
                GateDecision.REUSE_RECOMMENDED,
                {
                    "alias": payload["alias"],
                    "existing_alias_id": str(existing.id),
                },
            )
        return GateOutcome("alias", GateDecision.PASS)

    def _structural(self, proposal_type: ProposalType, payload: dict[str, Any]) -> GateOutcome:
        namespace_key = str(payload["namespace_key"])
        if proposal_type == ProposalType.CLASS:
            for parent_key in payload.get("parent_keys") or []:
                parent = self._ontology.get_class_by_key(
                    namespace_key=namespace_key, class_key=str(parent_key)
                )
                if parent is None:
                    return GateOutcome(
                        "structural",
                        GateDecision.FAIL,
                        {"unknown_parent_key": parent_key},
                    )
        if proposal_type == ProposalType.CLASS_PARENT:
            child = self._ontology.get_class_by_key(
                namespace_key=namespace_key, class_key=str(payload["child_key"])
            )
            parent = self._ontology.get_class_by_key(
                namespace_key=namespace_key, class_key=str(payload["parent_key"])
            )
            if child is None or parent is None:
                return GateOutcome(
                    "structural",
                    GateDecision.FAIL,
                    {
                        "child_found": child is not None,
                        "parent_found": parent is not None,
                    },
                )
        if proposal_type == ProposalType.ALIAS:
            target_type = AliasTargetType(str(payload["target_type"]))
            target_key = str(payload["target_key"])
            if target_type == AliasTargetType.CLASS:
                found = (
                    self._ontology.get_class_by_key(
                        namespace_key=namespace_key, class_key=target_key
                    )
                    is not None
                )
            else:
                found = (
                    self._ontology.get_predicate_by_key(
                        namespace_key=namespace_key, predicate_key=target_key
                    )
                    is not None
                )
            if not found:
                return GateOutcome(
                    "structural",
                    GateDecision.FAIL,
                    {"unknown_target_key": target_key, "target_type": target_type.value},
                )
        if proposal_type == ProposalType.PREDICATE:
            try:
                ValueKind(str(payload["value_kind"]))
            except ValueError:
                return GateOutcome(
                    "structural",
                    GateDecision.FAIL,
                    {"invalid_value_kind": payload.get("value_kind")},
                )
        return GateOutcome("structural", GateDecision.PASS)

    def _cycle(self, proposal_type: ProposalType, payload: dict[str, Any]) -> GateOutcome:
        if proposal_type not in {ProposalType.CLASS, ProposalType.CLASS_PARENT}:
            return GateOutcome("cycle", GateDecision.PASS)
        namespace_key = str(payload["namespace_key"])
        if proposal_type == ProposalType.CLASS_PARENT:
            child = self._ontology.get_class_by_key(
                namespace_key=namespace_key, class_key=str(payload["child_key"])
            )
            parent = self._ontology.get_class_by_key(
                namespace_key=namespace_key, class_key=str(payload["parent_key"])
            )
            if child is None or parent is None:
                return GateOutcome("cycle", GateDecision.PASS)
            # Cycle if parent already descends from child (or is the child).
            if child.id in self._ontology.list_ancestor_class_ids(parent.id):
                return GateOutcome(
                    "cycle",
                    GateDecision.FAIL,
                    {
                        "child_key": payload["child_key"],
                        "parent_key": payload["parent_key"],
                        "reason": "parent_is_descendant_of_child",
                    },
                )
        if proposal_type == ProposalType.CLASS:
            # New class cannot create a cycle via parents alone unless a parent key
            # equals the new key.
            new_key = str(payload["key"])
            if new_key in (payload.get("parent_keys") or []):
                return GateOutcome(
                    "cycle",
                    GateDecision.FAIL,
                    {"reason": "class_cannot_parent_itself", "key": new_key},
                )
        return GateOutcome("cycle", GateDecision.PASS)

    def _domain_range(self, proposal_type: ProposalType, payload: dict[str, Any]) -> GateOutcome:
        if proposal_type != ProposalType.PREDICATE:
            return GateOutcome("domain_range", GateDecision.PASS)
        namespace_key = str(payload["namespace_key"])
        for domain_key in payload.get("domain_keys") or []:
            domain = self._ontology.get_class_by_key(
                namespace_key=namespace_key, class_key=str(domain_key)
            )
            if domain is None:
                return GateOutcome(
                    "domain_range",
                    GateDecision.FAIL,
                    {"unknown_domain_key": domain_key},
                )
        value_kind = ValueKind(str(payload["value_kind"]))
        range_keys = list(payload.get("range_keys") or [])
        if value_kind == ValueKind.ENTITY and not range_keys:
            return GateOutcome(
                "domain_range",
                GateDecision.FAIL,
                {"reason": "entity_predicate_requires_range"},
            )
        if value_kind != ValueKind.ENTITY and range_keys:
            return GateOutcome(
                "domain_range",
                GateDecision.FAIL,
                {"reason": "non_entity_predicate_cannot_have_range"},
            )
        for range_key in range_keys:
            range_class = self._ontology.get_class_by_key(
                namespace_key=namespace_key, class_key=str(range_key)
            )
            if range_class is None:
                return GateOutcome(
                    "domain_range",
                    GateDecision.FAIL,
                    {"unknown_range_key": range_key},
                )
        return GateOutcome("domain_range", GateDecision.PASS)

    def _final_deterministic(
        self,
        proposal_type: ProposalType,
        payload: dict[str, Any],
        prior: list[GateOutcome],
    ) -> GateOutcome:
        deterministic = {
            "schema",
            "authorization",
            "existing_key",
            "alias",
            "structural",
            "cycle",
            "domain_range",
        }
        failures = [
            item.gate_name
            for item in prior
            if item.gate_name in deterministic and item.decision == GateDecision.FAIL
        ]
        if failures:
            return GateOutcome(
                "final_deterministic",
                GateDecision.FAIL,
                {"failed_gates": failures},
            )
        # Re-run structural/cycle/domain checks as a final barrier.
        for check in (
            self._structural(proposal_type, payload),
            self._cycle(proposal_type, payload),
            self._domain_range(proposal_type, payload),
        ):
            if check.decision == GateDecision.FAIL:
                return GateOutcome(
                    "final_deterministic",
                    GateDecision.FAIL,
                    {"recheck_gate": check.gate_name, **check.details},
                )
        return GateOutcome("final_deterministic", GateDecision.PASS)
