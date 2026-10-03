<p align="center">
  <img src="assets/logo.png" alt="AtlasSynapse logo" width="128" />
</p>

<p align="center">
  <img src="assets/wordmark.svg" alt="AtlasSynapse" width="420" />
</p>

Semantic memory and evolving knowledge graph for AI agents.

Brand tokens (charcoal `#303030`, coral `#E8705C`, Atlas thin / Synapse bold) live in [`assets/brand.yaml`](assets/brand.yaml).

AtlasSynapse is a self-hosted semantic memory service for storing, retrieving, connecting, revising, and reasoning over personal or domain knowledge. The physical database schema is stable while the semantic ontology evolves as governed data.

## Architectural commitments

- Python, PostgreSQL, SQLAlchemy, Alembic, Pydantic, and a FastAPI-style service layer.
- A thin MCP adapter over semantic service operations.
- A knowledge plane that performs deterministic routine writes without an LLM.
- An ontology control plane with deterministic gates and replaceable semantic review.
- Append, supersede, retract, and merge workflows with no hard-delete knowledge operations.
- Provenance, temporal validity, conflicts, auditability, and idempotency as first-class concerns.
- Optional `pgvector`; embeddings are derived data and never authoritative.

## Documentation map

- [Architecture](docs/architecture.md)
- [Ontology model](docs/ontology-model.md)
- [Invariants](docs/invariants.md)
- [MCP contract](docs/mcp-contract.md)
- [Error catalog](docs/error-catalog.md)
- [Development roadmap](docs/roadmap.md)
- [Cursor implementation guide](docs/cursor-implementation.md)
- [Architecture decision records](docs/adr/)

## Local development

```bash
python -m venv .venv
source .venv/bin/activate
make install
make infra-up
make migrate
make test
```

Useful targets:

- `make lint` / `make format` / `make typecheck` / `make ci`
- `make seed` for re-running the idempotent core ontology seed
- `semantic-memory` to start the HTTP app (health endpoints)

Health endpoints:

- `GET /health/live`
- `GET /health/ready`

Phase 0/1 implements the repository skeleton, ORM models, initial migration, core ontology seed, and health checks. Knowledge-plane services begin in Phase 2.
