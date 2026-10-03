"""Persistence repositories."""

from semantic_memory.repositories.actors import ActorRepository
from semantic_memory.repositories.entities import EntityRepository
from semantic_memory.repositories.idempotency import IdempotencyRepository
from semantic_memory.repositories.ontology import OntologyRepository
from semantic_memory.repositories.statements import StatementRepository

__all__ = [
    "ActorRepository",
    "EntityRepository",
    "IdempotencyRepository",
    "OntologyRepository",
    "StatementRepository",
]
