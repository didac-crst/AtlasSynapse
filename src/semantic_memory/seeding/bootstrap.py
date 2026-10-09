"""Idempotent system ontology bootstrap for deploy and CLI seed."""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from semantic_memory.seeding.claim_ontology import ensure_claim_ontology
from semantic_memory.seeding.identity_graph_ontology import ensure_identity_graph_ontology
from semantic_memory.seeding.ontology import ensure_rich_event_models, seed_core_ontology
from semantic_memory.seeding.smoke_namespace import ensure_smoke_namespace


def bootstrap_system_ontology(session: Session) -> dict[str, Any]:
    """Ensure core ontology and required system extensions (identity graph, rich events)."""
    core = seed_core_ontology(session)
    claim = ensure_claim_ontology(session)
    identity_graph = ensure_identity_graph_ontology(session)
    rich_events = ensure_rich_event_models(session)
    smoke = ensure_smoke_namespace(session)
    return {
        "core": core,
        "claim": claim,
        "identity_graph": identity_graph,
        "rich_events": rich_events,
        "smoke": smoke,
    }
