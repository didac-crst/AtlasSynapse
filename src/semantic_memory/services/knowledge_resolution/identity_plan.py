"""Identity probing via existing IdentityService (no entity creates)."""

from __future__ import annotations

from sqlalchemy.orm import Session

from semantic_memory.models.enums import EntityStatus
from semantic_memory.repositories.entities import EntityRepository
from semantic_memory.schemas.identity import IdentityResolutionOutcome
from semantic_memory.services.identity import IdentityService
from semantic_memory.services.knowledge_continuation.schemas import (
    IdentityAnswerResolution,
    IdentityOverride,
)
from semantic_memory.services.knowledge_resolution.schemas import (
    EntityBindPlan,
    ObjectBindPlan,
    ProjectionDisposition,
    ResolutionWarning,
    ResolutionWarningCode,
)


def plan_entity_projection(
    session: Session,
    *,
    text: str | None,
    required: bool,
    field: str,
    adjudicate: bool = False,
    override: IdentityOverride | None = None,
) -> tuple[EntityBindPlan | ObjectBindPlan, list[ResolutionWarning], bool, bool]:
    """Return (plan, warnings, needs_required_blocker, discard_candidate).

    ``needs_required_blocker`` is True only when ``required`` and identity is
    ambiguous (cannot safely plan reuse or create).
    ``discard_candidate`` is True when an answered clarification rejected the identity.
    """
    warnings: list[ResolutionWarning] = []

    if override is not None:
        return _apply_override(
            session,
            text=text,
            required=required,
            field=field,
            override=override,
            warnings=warnings,
        )

    if text is None or not str(text).strip():
        if required:
            return (
                EntityBindPlan(
                    disposition=ProjectionDisposition.CLARIFY,
                    detail=f"empty_required_{field}",
                ),
                warnings,
                True,
                False,
            )
        return (
            EntityBindPlan(disposition=ProjectionDisposition.OMIT, detail=f"empty_{field}"),
            warnings,
            False,
            False,
        )

    cleaned = str(text).strip()
    result = IdentityService(session).resolve(
        canonical_name=cleaned,
        adjudicate=adjudicate,
    )
    identity = result.identity
    if identity is None:
        plan = EntityBindPlan(
            disposition=ProjectionDisposition.CREATE,
            text=cleaned,
            detail="no_identity_match_future_create",
        )
        return plan, warnings, False, False

    if identity.resolution == IdentityResolutionOutcome.MATCH and identity.entity_id:
        return (
            EntityBindPlan(
                disposition=ProjectionDisposition.REUSE,
                text=cleaned,
                entity_id=identity.entity_id,
                detail="identity_match",
            ),
            warnings,
            False,
            False,
        )

    if identity.resolution == IdentityResolutionOutcome.AMBIGUOUS:
        candidate_ids = [c.entity_id for c in identity.candidates]
        if required:
            return (
                EntityBindPlan(
                    disposition=ProjectionDisposition.CLARIFY,
                    text=cleaned,
                    candidate_entity_ids=candidate_ids,
                    detail="ambiguous_required_identity",
                ),
                warnings,
                True,
                False,
            )
        warnings.append(
            ResolutionWarning(
                code=ResolutionWarningCode.AMBIGUOUS_OPTIONAL_PROJECTION,
                detail=f"optional {field} ambiguous; omitting projection",
                field=field,
            )
        )
        return (
            EntityBindPlan(
                disposition=ProjectionDisposition.OMIT,
                text=cleaned,
                candidate_entity_ids=candidate_ids,
                detail="ambiguous_optional_omitted",
            ),
            warnings,
            False,
            False,
        )

    return (
        EntityBindPlan(
            disposition=ProjectionDisposition.CREATE,
            text=cleaned,
            detail="no_match_future_create",
        ),
        warnings,
        False,
        False,
    )


def _apply_override(
    session: Session,
    *,
    text: str | None,
    required: bool,
    field: str,
    override: IdentityOverride,
    warnings: list[ResolutionWarning],
) -> tuple[EntityBindPlan, list[ResolutionWarning], bool, bool]:
    cleaned = None if text is None else str(text).strip()

    if override.resolution == IdentityAnswerResolution.REJECT:
        if required:
            return (
                EntityBindPlan(
                    disposition=ProjectionDisposition.OMIT,
                    text=cleaned,
                    detail="identity_rejected_by_clarification",
                ),
                warnings,
                False,
                True,
            )
        return (
            EntityBindPlan(
                disposition=ProjectionDisposition.OMIT,
                text=cleaned,
                detail="identity_rejected_optional",
            ),
            warnings,
            False,
            False,
        )

    if override.resolution == IdentityAnswerResolution.CREATE_NEW:
        return (
            EntityBindPlan(
                disposition=ProjectionDisposition.CREATE,
                text=cleaned,
                detail="identity_create_new_from_clarification",
            ),
            warnings,
            False,
            False,
        )

    # chosen_entity — revalidate current state; never bypass existence checks.
    chosen = override.chosen_entity_id
    if chosen is None:
        return (
            EntityBindPlan(
                disposition=ProjectionDisposition.CLARIFY,
                text=cleaned,
                candidate_entity_ids=list(override.offered_entity_ids),
                detail="chosen_entity_missing_id",
            ),
            warnings,
            required,
            False,
        )

    if override.offered_entity_ids and chosen not in set(override.offered_entity_ids):
        # Stale/illegal choice — re-open ambiguity if still ambiguous.
        return _reprobe_or_stale(
            session,
            cleaned=cleaned,
            required=required,
            field=field,
            warnings=warnings,
            detail="chosen_entity_not_in_offered_set",
        )

    entity = EntityRepository(session).get(chosen)
    if entity is None or entity.status != EntityStatus.ACTIVE.value:
        return _reprobe_or_stale(
            session,
            cleaned=cleaned,
            required=required,
            field=field,
            warnings=warnings,
            detail="chosen_entity_no_longer_active",
        )

    # Re-probe current identity state. Accept reuse only when the choice still
    # makes sense against live candidates; never silently keep a stale answer.
    if cleaned:
        probed = IdentityService(session).resolve(canonical_name=cleaned, adjudicate=False)
        identity = probed.identity
        if identity is not None and identity.resolution == IdentityResolutionOutcome.AMBIGUOUS:
            live_ids = {c.entity_id for c in identity.candidates}
            if chosen not in live_ids:
                return (
                    EntityBindPlan(
                        disposition=ProjectionDisposition.CLARIFY,
                        text=cleaned,
                        candidate_entity_ids=list(live_ids),
                        detail="chosen_entity_stale_vs_current_ambiguity",
                    ),
                    warnings,
                    required,
                    False,
                )
        elif identity is not None and identity.resolution == IdentityResolutionOutcome.MATCH:
            if identity.entity_id != chosen:
                return _reprobe_or_stale(
                    session,
                    cleaned=cleaned,
                    required=required,
                    field=field,
                    warnings=warnings,
                    detail="chosen_entity_stale_vs_current_match",
                )
        elif identity is not None and identity.resolution == IdentityResolutionOutcome.NO_MATCH:
            # Chosen entity exists but no longer participates in identity for this
            # name — treat as stale rather than forcing a reuse the graph no longer supports.
            return _reprobe_or_stale(
                session,
                cleaned=cleaned,
                required=required,
                field=field,
                warnings=warnings,
                detail="chosen_entity_stale_vs_no_match",
            )

    return (
        EntityBindPlan(
            disposition=ProjectionDisposition.REUSE,
            text=cleaned,
            entity_id=chosen,
            detail="identity_chosen_entity_revalidated",
        ),
        warnings,
        False,
        False,
    )


def _reprobe_or_stale(
    session: Session,
    *,
    cleaned: str | None,
    required: bool,
    field: str,
    warnings: list[ResolutionWarning],
    detail: str,
) -> tuple[EntityBindPlan, list[ResolutionWarning], bool, bool]:
    del field
    if cleaned:
        probed = IdentityService(session).resolve(canonical_name=cleaned, adjudicate=False)
        identity = probed.identity
        if identity is not None and identity.resolution == IdentityResolutionOutcome.AMBIGUOUS:
            ids = [c.entity_id for c in identity.candidates]
            return (
                EntityBindPlan(
                    disposition=ProjectionDisposition.CLARIFY,
                    text=cleaned,
                    candidate_entity_ids=ids,
                    detail=detail,
                ),
                warnings,
                required,
                False,
            )
        if identity is not None and identity.resolution == IdentityResolutionOutcome.NO_MATCH:
            return (
                EntityBindPlan(
                    disposition=ProjectionDisposition.CREATE,
                    text=cleaned,
                    detail=detail,
                ),
                warnings,
                False,
                False,
            )
    return (
        EntityBindPlan(
            disposition=ProjectionDisposition.CLARIFY if required else ProjectionDisposition.OMIT,
            text=cleaned,
            detail=detail,
        ),
        warnings,
        required,
        False,
    )


def plan_subject(
    session: Session,
    *,
    text: str | None,
    required: bool,
    adjudicate: bool = False,
    override: IdentityOverride | None = None,
) -> tuple[EntityBindPlan, list[ResolutionWarning], bool, bool]:
    plan, warnings, blocker, discard = plan_entity_projection(
        session,
        text=text,
        required=required,
        field="subject",
        adjudicate=adjudicate,
        override=override,
    )
    assert isinstance(plan, EntityBindPlan)
    return plan, warnings, blocker, discard


def plan_object_entity(
    session: Session,
    *,
    text: str | None,
    required: bool,
    adjudicate: bool = False,
    override: IdentityOverride | None = None,
) -> tuple[ObjectBindPlan, list[ResolutionWarning], bool, bool]:
    plan, warnings, blocker, discard = plan_entity_projection(
        session,
        text=text,
        required=required,
        field="object",
        adjudicate=adjudicate,
        override=override,
    )
    assert isinstance(plan, EntityBindPlan)
    return (
        ObjectBindPlan(
            disposition=plan.disposition,
            text=plan.text,
            entity_id=plan.entity_id,
            class_key=plan.class_key,
            candidate_entity_ids=plan.candidate_entity_ids,
            detail=plan.detail,
        ),
        warnings,
        blocker,
        discard,
    )
