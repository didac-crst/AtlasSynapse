"""Route quality findings to existing mechanisms or residual issues."""

from __future__ import annotations

from semantic_memory.models.enums import QualityRouteOutcome
from semantic_memory.schemas.memory_quality import QualityFinding


def route_finding(finding: QualityFinding) -> QualityRouteOutcome:
    """Decide persistence side effects for a finding.

    Post-write router does not emit REQUEST_CLARIFICATION.
    """
    if finding.recommended_route == QualityRouteOutcome.IGNORE:
        return QualityRouteOutcome.IGNORE
    if finding.recommended_route == QualityRouteOutcome.UPSERT_CONFLICT:
        return QualityRouteOutcome.UPSERT_CONFLICT
    if finding.recommended_route == QualityRouteOutcome.AUTO_RESOLVE:
        return QualityRouteOutcome.AUTO_RESOLVE
    if finding.recommended_route == QualityRouteOutcome.IDENTITY_REVIEW:
        return QualityRouteOutcome.IDENTITY_REVIEW
    return QualityRouteOutcome.OPEN_QUALITY_ISSUE
