"""Post-mutation projections for statement write responses (agent UX)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from semantic_memory.models import StatementStatus
from semantic_memory.models.enums import ValueKind
from semantic_memory.schemas.dry_run import StatementWriteAction
from semantic_memory.schemas.identity import IdentityAction, IdentityResolutionResult
from semantic_memory.schemas.statements import (
    CONTEXTUAL_RELATED_FACTS_MAX,
    EFFECTIVE_STATE_MAX_VALUES,
    AssertionOutcome,
    AssertStatementResponse,
    ClarificationAction,
    ClarificationCandidate,
    EffectiveState,
    EffectiveStateValue,
    MutationChanges,
    MutationContextSlice,
    RelatedFact,
    RetractStatementResponse,
    StatementResponse,
    StatementReturnMode,
    SupersedeStatementResponse,
)

if TYPE_CHECKING:
    from semantic_memory.services.statements import StatementService


def build_clarification_action(
    *,
    subject_identity: IdentityResolutionResult | None,
    object_identity: IdentityResolutionResult | None,
    clarification_request_id: uuid.UUID | None = None,
) -> ClarificationAction | None:
    ambiguous: list[tuple[str, IdentityResolutionResult]] = []
    if subject_identity is not None and subject_identity.action == IdentityAction.CLARIFY:
        ambiguous.append(("subject", subject_identity))
    if object_identity is not None and object_identity.action == IdentityAction.CLARIFY:
        ambiguous.append(("object", object_identity))
    if not ambiguous:
        return None

    candidates: list[ClarificationCandidate] = []
    for side, identity in ambiguous:
        for cand in identity.candidates:
            candidates.append(
                ClarificationCandidate(
                    entity_id=cand.entity_id,
                    canonical_name=cand.canonical_name,
                    types=list(cand.class_keys),
                    match_reasons=[ev.signal for ev in cand.reasons],
                    side=side,
                )
            )
    side_label = ambiguous[0][0]
    name_hint = ""
    if candidates:
        name_hint = candidates[0].canonical_name
    question = (
        f"Which {side_label} do you mean?"
        if not name_hint
        else f"Which {name_hint} do you mean ({side_label})?"
    )
    return ClarificationAction(
        clarification_request_id=clarification_request_id,
        question=question,
        reason="multiple_identity_candidates",
        candidates=candidates,
        resume_with="answer_identity_clarification",
    )


def apply_return_mode_assert(
    response: AssertStatementResponse,
    *,
    mode: StatementReturnMode,
) -> AssertStatementResponse:
    if mode == StatementReturnMode.MINIMAL:
        return AssertStatementResponse(
            outcome=response.outcome,
            statement=None,
            request_id=response.request_id,
            reused=response.reused,
            statement_action=response.statement_action,
            clarification_request_id=response.clarification_request_id,
            dry_run=response.dry_run,
            operation_mode=response.operation_mode,
            would_persist=response.would_persist,
            changes=MutationChanges(
                statement_action=response.statement_action,
                created_statement_id=(
                    response.statement.id
                    if response.statement is not None and response.outcome == AssertionOutcome.CREATE
                    else None
                ),
                reused_statement_id=(
                    response.statement.id
                    if response.statement is not None and response.outcome == AssertionOutcome.REUSE
                    else None
                ),
            ),
        )
    if mode == StatementReturnMode.STANDARD:
        return response.model_copy(update={"context": None})
    return response


def apply_return_mode_supersede(
    response: SupersedeStatementResponse,
    *,
    mode: StatementReturnMode,
) -> SupersedeStatementResponse:
    if mode == StatementReturnMode.MINIMAL:
        return SupersedeStatementResponse(
            outcome=response.outcome,
            previous_statement=response.previous_statement,
            statement=response.statement,
            request_id=response.request_id,
            statement_action=response.statement_action,
            dry_run=response.dry_run,
            operation_mode=response.operation_mode,
            would_persist=response.would_persist,
            changes=MutationChanges(
                statement_action=response.statement_action,
                created_statement_id=response.statement.id,
                superseded_statement_ids=[response.previous_statement.id],
            ),
        )
    if mode == StatementReturnMode.STANDARD:
        return response.model_copy(update={"context": None})
    return response


def apply_return_mode_retract(
    response: RetractStatementResponse,
    *,
    mode: StatementReturnMode,
) -> RetractStatementResponse:
    if mode == StatementReturnMode.MINIMAL:
        return RetractStatementResponse(
            outcome=response.outcome,
            statement=response.statement,
            request_id=response.request_id,
            reason=response.reason,
            statement_action=response.statement_action,
            dry_run=response.dry_run,
            operation_mode=response.operation_mode,
            would_persist=response.would_persist,
            changes=MutationChanges(
                statement_action=response.statement_action,
                retracted_statement_ids=[response.statement.id],
            ),
        )
    if mode == StatementReturnMode.STANDARD:
        return response.model_copy(update={"context": None})
    return response


class MutationProjectionMixin:
    """Helpers mixed into StatementService for effective_state / contextual slices."""

    def _build_effective_state(
        self: StatementService,
        *,
        subject_entity_id: uuid.UUID,
        predicate_id: uuid.UUID,
        predicate_key: str,
        namespace_key: str,
    ) -> EffectiveState:
        survivor = self._entities.resolve_survivor_id(subject_entity_id)
        rows = self._statements.find_asserted_for_predicate(
            subject_entity_id=survivor,
            predicate_id=predicate_id,
        )
        # Also include statements whose subject is in the identity group but not yet
        # rewritten to survivor (FK preserved on merge).
        identity_ids = set(self._entities.identity_group_ids(survivor))
        if identity_ids - {survivor}:
            extra: list = []
            for eid in identity_ids:
                if eid == survivor:
                    continue
                extra.extend(
                    self._statements.find_asserted_for_predicate(
                        subject_entity_id=eid,
                        predicate_id=predicate_id,
                    )
                )
            seen = {row.id for row in rows}
            for row in extra:
                if row.id not in seen:
                    rows.append(row)
                    seen.add(row.id)

        rows.sort(key=lambda s: (s.asserted_at, s.id), reverse=True)
        truncated = len(rows) > EFFECTIVE_STATE_MAX_VALUES
        rows = rows[:EFFECTIVE_STATE_MAX_VALUES]
        values: list[EffectiveStateValue] = []
        for row in rows:
            dto = self.to_response(row)
            canonical_name = None
            if dto.object_entity_id is not None:
                obj = self._entities.get(dto.object_entity_id)
                if obj is not None:
                    canonical_name = obj.canonical_name
            values.append(_value_from_statement(dto, canonical_name=canonical_name))
        return EffectiveState(
            subject_entity_id=survivor,
            predicate_key=predicate_key,
            namespace_key=namespace_key,
            values=values,
            truncated=truncated,
        )

    def _build_contextual_slice(
        self: StatementService,
        *,
        statement: StatementResponse,
    ) -> MutationContextSlice:
        """Fixed contract: max 5, effective only, one-hop, same scorer, no get_relevant_context."""
        from semantic_memory.services.retrieval import RetrievalService

        focus_ids = {statement.subject_entity_id}
        if statement.object_entity_id is not None:
            focus_ids.add(statement.object_entity_id)

        retrieval = RetrievalService(self._session)
        now = datetime.now(UTC)
        scored: list[RelatedFact] = []
        seen: set[uuid.UUID] = set()
        specificity_cache: dict[uuid.UUID, float] = {}

        for focus_id in focus_ids:
            identity_ids = retrieval._entities.identity_group_ids(focus_id)
            survivor = retrieval._entities.resolve_survivor_id(focus_id)
            timeline = retrieval._statements.list_for_entity_timeline(identity_ids)
            candidates = [
                row
                for row in timeline
                if row.status == StatementStatus.ASSERTED.value and row.id != statement.id
            ]
            evidence_stats = retrieval._provenance_repo.evidence_stats_for_statements(
                [row.id for row in candidates]
            )
            for row in candidates:
                if row.id in seen:
                    continue
                seen.add(row.id)
                count, reliability = evidence_stats.get(row.id, (0, 0.0))
                signals, _ = retrieval._score_statement(
                    row,
                    query=None,
                    focus_entity_id=survivor,
                    now=now,
                    evidence_count=count,
                    source_reliability=reliability,
                    specificity_cache=specificity_cache,
                )
                subject_in = row.subject_entity_id in set(identity_ids)
                object_in = (
                    row.object_entity_id is not None and row.object_entity_id in set(identity_ids)
                )
                if subject_in and not object_in:
                    direction = "outgoing"
                    neighbor = row.object_entity_id
                elif object_in and not subject_in:
                    direction = "incoming"
                    neighbor = row.subject_entity_id
                else:
                    direction = "outgoing"
                    neighbor = survivor
                if neighbor is not None:
                    neighbor = retrieval._entities.resolve_survivor_id(neighbor)
                scored.append(
                    RelatedFact(
                        statement=self.to_response(row),
                        ranking_score=signals.ranking_score,
                        direction=direction,
                        neighbor_entity_id=neighbor,
                    )
                )

        scored.sort(key=lambda item: item.ranking_score, reverse=True)
        return MutationContextSlice(
            related_facts=scored[:CONTEXTUAL_RELATED_FACTS_MAX],
            related_facts_limit=CONTEXTUAL_RELATED_FACTS_MAX,
        )

    def _project_assert(
        self: StatementService,
        response: AssertStatementResponse,
        *,
        mode: StatementReturnMode,
    ) -> AssertStatementResponse:
        updates: dict = {}
        if response.outcome == AssertionOutcome.CLARIFY:
            updates["clarification"] = build_clarification_action(
                subject_identity=response.subject_identity,
                object_identity=response.object_identity,
                clarification_request_id=response.clarification_request_id,
            )
            updates["statement_action"] = (
                response.statement_action or StatementWriteAction.NOT_WRITTEN
            )
            updates["changes"] = MutationChanges(
                statement_action=updates["statement_action"],
            )
            enriched = response.model_copy(update=updates)
            return apply_return_mode_assert(enriched, mode=mode)

        if response.statement is None:
            return apply_return_mode_assert(response, mode=mode)

        stmt = response.statement
        action = response.statement_action
        if action is None:
            action = (
                StatementWriteAction.REUSE
                if response.outcome == AssertionOutcome.REUSE
                else StatementWriteAction.CREATE
            )
        updates["statement_action"] = action
        updates["changes"] = MutationChanges(
            statement_action=action,
            created_statement_id=stmt.id if response.outcome == AssertionOutcome.CREATE else None,
            reused_statement_id=stmt.id if response.outcome == AssertionOutcome.REUSE else None,
            conflicts_created=list(response.conflict_ids),
        )
        updates["effective_state"] = self._build_effective_state(
            subject_entity_id=stmt.subject_entity_id,
            predicate_id=stmt.predicate_id,
            predicate_key=stmt.predicate_key,
            namespace_key=stmt.namespace_key,
        )
        if mode == StatementReturnMode.CONTEXTUAL:
            updates["context"] = self._build_contextual_slice(statement=stmt)
        enriched = response.model_copy(update=updates)
        return apply_return_mode_assert(enriched, mode=mode)

    def _project_supersede(
        self: StatementService,
        response: SupersedeStatementResponse,
        *,
        mode: StatementReturnMode,
        created: AssertStatementResponse | None = None,
    ) -> SupersedeStatementResponse:
        action = response.statement_action or StatementWriteAction.SUPERSEDE
        updates: dict = {
            "outcome": AssertionOutcome.SUPERSEDE,
            "statement_action": action,
            "changes": MutationChanges(
                statement_action=action,
                created_statement_id=response.statement.id,
                superseded_statement_ids=[response.previous_statement.id],
                conflicts_created=list(
                    created.conflict_ids if created is not None else response.conflict_ids
                ),
            ),
            "effective_state": self._build_effective_state(
                subject_entity_id=response.statement.subject_entity_id,
                predicate_id=response.statement.predicate_id,
                predicate_key=response.statement.predicate_key,
                namespace_key=response.statement.namespace_key,
            ),
        }
        if created is not None:
            updates["subject_identity"] = created.subject_identity
            updates["object_identity"] = created.object_identity
            updates["conflict_ids"] = list(created.conflict_ids)
            updates["reused"] = created.reused
        if mode == StatementReturnMode.CONTEXTUAL:
            updates["context"] = self._build_contextual_slice(statement=response.statement)
        enriched = response.model_copy(update=updates)
        return apply_return_mode_supersede(enriched, mode=mode)

    def _project_retract(
        self: StatementService,
        response: RetractStatementResponse,
        *,
        mode: StatementReturnMode,
    ) -> RetractStatementResponse:
        action = response.statement_action or StatementWriteAction.RETRACT
        stmt = response.statement
        updates: dict = {
            "outcome": AssertionOutcome.RETRACT,
            "statement_action": action,
            "changes": MutationChanges(
                statement_action=action,
                retracted_statement_ids=[stmt.id],
            ),
            "effective_state": self._build_effective_state(
                subject_entity_id=stmt.subject_entity_id,
                predicate_id=stmt.predicate_id,
                predicate_key=stmt.predicate_key,
                namespace_key=stmt.namespace_key,
            ),
        }
        if mode == StatementReturnMode.CONTEXTUAL:
            updates["context"] = self._build_contextual_slice(statement=stmt)
        enriched = response.model_copy(update=updates)
        return apply_return_mode_retract(enriched, mode=mode)


def _value_from_statement(
    dto: StatementResponse, *, canonical_name: str | None = None
) -> EffectiveStateValue:
    if dto.object_entity_id is not None:
        kind = ValueKind.ENTITY.value
    elif dto.object_string is not None:
        kind = ValueKind.STRING.value
    elif dto.object_number is not None:
        kind = ValueKind.NUMBER.value
    elif dto.object_boolean is not None:
        kind = ValueKind.BOOLEAN.value
    elif dto.object_datetime is not None:
        kind = ValueKind.DATETIME.value
    else:
        kind = ValueKind.JSON.value
    return EffectiveStateValue(
        statement_id=dto.id,
        value_kind=kind,
        entity_id=dto.object_entity_id,
        canonical_name=canonical_name,
        object_string=dto.object_string,
        object_number=dto.object_number,
        object_boolean=dto.object_boolean,
        object_datetime=dto.object_datetime,
        object_json=dto.object_json,
        asserted_at=dto.asserted_at,
    )
