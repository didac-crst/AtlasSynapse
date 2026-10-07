"""Disposable smoke-test ontology namespace (not for production concepts)."""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from semantic_memory.models.ontology import OntologyNamespace
from semantic_memory.seeding.ontology import stable_seed_id

SMOKE_NAMESPACE_KEY = "smoke"


def ensure_smoke_namespace(session: Session) -> dict[str, Any]:
    """Ensure an empty ``smoke`` namespace exists for disposable test concepts.

    Production proposals and semantic-review lexical search are namespace-scoped
    to the proposal's namespace (usually ``core``), so concepts created under
    ``smoke`` do not pollute production similarity/review.
    """
    existing = session.scalar(
        select(OntologyNamespace).where(OntologyNamespace.key == SMOKE_NAMESPACE_KEY)
    )
    if existing is not None:
        return {"namespace": SMOKE_NAMESPACE_KEY, "created": False}
    session.add(
        OntologyNamespace(
            id=stable_seed_id("namespace", SMOKE_NAMESPACE_KEY),
            key=SMOKE_NAMESPACE_KEY,
            label="Smoke",
            description=(
                "Disposable ontology namespace for smoke/integration tests. "
                "Do not put production biography or business concepts here."
            ),
        )
    )
    session.flush()
    return {"namespace": SMOKE_NAMESPACE_KEY, "created": True}
