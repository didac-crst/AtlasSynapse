"""Deterministic seed helpers."""

from semantic_memory.seeding.bootstrap import bootstrap_system_ontology
from semantic_memory.seeding.ontology import (
    CORE_INHERITANCE,
    CORE_NAMESPACE_KEY,
    ensure_rich_event_models,
    seed_core_ontology,
)
from semantic_memory.seeding.smoke_namespace import SMOKE_NAMESPACE_KEY, ensure_smoke_namespace

__all__ = [
    "CORE_INHERITANCE",
    "CORE_NAMESPACE_KEY",
    "SMOKE_NAMESPACE_KEY",
    "bootstrap_system_ontology",
    "ensure_rich_event_models",
    "ensure_smoke_namespace",
    "seed_core_ontology",
]
