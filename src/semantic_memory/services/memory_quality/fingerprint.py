"""Problem-identity fingerprints for memory quality issues."""

from __future__ import annotations

import hashlib
import uuid

from semantic_memory.models.enums import MemoryQualityIssueType


def build_quality_fingerprint(
    *,
    issue_type: MemoryQualityIssueType | str,
    detector_key: str,
    entity_ids: list[uuid.UUID] | None = None,
    statement_ids: list[uuid.UUID] | None = None,
) -> str:
    """Fingerprint the problem identity — not detector implementation version."""
    parts = [
        str(issue_type),
        detector_key,
        ",".join(sorted(str(e) for e in (entity_ids or []))),
        ",".join(sorted(str(s) for s in (statement_ids or []))),
    ]
    raw = "|".join(parts)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()
