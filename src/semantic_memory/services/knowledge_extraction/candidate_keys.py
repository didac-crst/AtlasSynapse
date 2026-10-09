"""Structural candidate identity and semantic fingerprinting.

Algorithm (Phase C V1)
----------------------
``candidate_key`` identifies the stable extraction *slot* within an ingestion:

    candidate_key = "c_" + sha256_hex32(json(source_context_path) + "|" + ordinal)

The source revision is fixed on the ingestion; later Claim identity is:

    source_content_revision_id + candidate_key

Semantic content is **not** part of ``candidate_key``.

``semantic_fingerprint`` captures the staged meaning:

    kind | polarity | epistemic_status | derivation
    | normalized claim_text | canonical claim_payload

Re-extraction:

    same key + same fingerprint → no-op
    same key + different fingerprint → KnowledgeExtractionConflictError
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from typing import Any

_WS_RE = re.compile(r"\s+")

# Volatile / non-semantic annotation keys excluded from fingerprints.
_PAYLOAD_EXCLUDE = frozenset(
    {
        "classification_basis",
        "extractor_version",
    }
)


def normalize_claim_text(text: str) -> str:
    return _WS_RE.sub(" ", text.strip()).casefold()


def build_candidate_key(
    *,
    source_context_path: Sequence[str | int],
    ordinal: int = 0,
) -> str:
    """Stable structural slot id (unique within an ingestion via DB constraint)."""
    path_json = json.dumps(list(source_context_path), ensure_ascii=False, separators=(",", ":"))
    material = f"{path_json}|{ordinal}"
    digest = hashlib.sha256(material.encode("utf-8")).hexdigest()[:32]
    return f"c_{digest}"


def canonical_claim_payload(payload: Mapping[str, Any] | None) -> Any:
    """Stable JSON-ready payload for fingerprinting (drops volatile annotations)."""
    if not payload:
        return {}
    cleaned = {k: v for k, v in payload.items() if k not in _PAYLOAD_EXCLUDE}
    return json.loads(json.dumps(cleaned, sort_keys=True, default=str))


def semantic_fingerprint(
    *,
    kind: str,
    polarity: str,
    epistemic_status: str,
    derivation: str,
    claim_text: str,
    claim_payload: Mapping[str, Any] | None = None,
) -> str:
    snapshot = {
        "kind": kind,
        "polarity": polarity,
        "epistemic_status": epistemic_status,
        "derivation": derivation,
        "claim_text": normalize_claim_text(claim_text),
        "claim_payload": canonical_claim_payload(claim_payload),
    }
    material = json.dumps(snapshot, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:32]
