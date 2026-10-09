"""Stable clarification_key fingerprints (no timestamps / prose)."""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass


@dataclass(frozen=True)
class IdentitySnapshotEntry:
    """Resolution-relevant identity material for fingerprinting.

    Excludes timestamps, prose, scores, ranking text, and transient evidence wording.
    """

    entity_id: uuid.UUID
    status: str
    class_keys: tuple[str, ...]


def identity_clarification_key(
    *,
    candidate_id: uuid.UUID,
    field: str,
    snapshot: list[IdentitySnapshotEntry],
) -> str:
    """Fingerprint identity ambiguity by root + field + canonicalized snapshot.

    Same meaningful ambiguity → same key.
    Materially changed identity options/state (status / class keys) → new key.
    """
    lines: list[str] = []
    for entry in sorted(snapshot, key=lambda e: str(e.entity_id)):
        classes = ",".join(sorted(entry.class_keys))
        lines.append(f"{entry.entity_id}|{entry.status}|{classes}")
    digest = hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()[:16]
    return f"identity:{candidate_id}:{field}:{digest}"


def ontology_clarification_key(
    *,
    ontology_clarification_request_id: uuid.UUID | None = None,
    ontology_proposal_id: uuid.UUID | None = None,
) -> str:
    if ontology_clarification_request_id is not None:
        return f"ontology:clarification:{ontology_clarification_request_id}"
    if ontology_proposal_id is not None:
        return f"ontology:proposal:{ontology_proposal_id}"
    raise ValueError("ontology clarification key requires a clarification or proposal id")


def policy_clarification_key(*, candidate_id: uuid.UUID, ref: str | None, detail: str) -> str:
    material = f"{candidate_id}|{ref or ''}|{detail}"
    digest = hashlib.sha256(material.encode("utf-8")).hexdigest()[:16]
    return f"policy:{candidate_id}:{digest}"
