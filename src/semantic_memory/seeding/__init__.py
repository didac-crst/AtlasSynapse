"""Deterministic seed helpers."""

from semantic_memory.seeding.bootstrap import bootstrap_system_ontology
from semantic_memory.seeding.ontology import (
    CORE_INHERITANCE,
    CORE_NAMESPACE_KEY,
    ensure_rich_event_models,
    seed_core_ontology,
)

__all__ = [
    "CORE_INHERITANCE",
    "CORE_NAMESPACE_KEY",
    "bootstrap_system_ontology",
    "ensure_rich_event_models",
    "seed_core_ontology",
]
