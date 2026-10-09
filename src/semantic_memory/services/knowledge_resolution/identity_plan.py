"""Identity probing via existing IdentityService (no entity creates)."""

from __future__ import annotations

from sqlalchemy.orm import Session

from semantic_memory.schemas.identity import IdentityResolutionOutcome
from semantic_memory.services.identity import IdentityService
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
) -> tuple[EntityBindPlan | ObjectBindPlan, list[ResolutionWarning], bool]:
    """Return (plan, warnings, needs_required_blocker).

    ``needs_required_blocker`` is True only when ``required`` and identity is
    ambiguous (cannot safely plan reuse or create).
    """
    warnings: list[ResolutionWarning] = []
    if text is None or not str(text).strip():
        if required:
            return (
                EntityBindPlan(
                    disposition=ProjectionDisposition.CLARIFY,
                    detail=f"empty_required_{field}",
                ),
                warnings,
                True,
            )
        return (
            EntityBindPlan(disposition=ProjectionDisposition.OMIT, detail=f"empty_{field}"),
            warnings,
            False,
        )

    cleaned = str(text).strip()
    # IdentityService with adjudicate=False keeps Phase D deterministic/offline.
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
        return plan, warnings, False

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
        )

    # NO_MATCH → future create plan (no Entity written).
    return (
        EntityBindPlan(
            disposition=ProjectionDisposition.CREATE,
            text=cleaned,
            detail="no_match_future_create",
        ),
        warnings,
        False,
    )


def plan_subject(
    session: Session, *, text: str | None, required: bool, adjudicate: bool = False
) -> tuple[EntityBindPlan, list[ResolutionWarning], bool]:
    plan, warnings, blocker = plan_entity_projection(
        session, text=text, required=required, field="subject", adjudicate=adjudicate
    )
    assert isinstance(plan, EntityBindPlan)
    return plan, warnings, blocker


def plan_object_entity(
    session: Session, *, text: str | None, required: bool, adjudicate: bool = False
) -> tuple[ObjectBindPlan, list[ResolutionWarning], bool]:
    plan, warnings, blocker = plan_entity_projection(
        session, text=text, required=required, field="object", adjudicate=adjudicate
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
    )
