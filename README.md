# AtlasSynapse

Semantic memory and evolving knowledge graph for AI agents.

AtlasSynapse is planned as a self-hosted semantic memory service for storing, retrieving, connecting, revising, and reasoning over personal or domain knowledge. The physical database schema is stable while the semantic ontology evolves as governed data.

This repository is currently an architecture scaffold. It intentionally contains documentation, repository boundaries, configuration placeholders, and implementation guidance for Cursor. Application code, migrations, and business logic are not implemented in this scaffold.

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

## Planned repository layout

The target Python package layout is described in [architecture.md](docs/architecture.md) and mirrored by the empty boundary directories under `src/semantic_memory/`. Those directories are placeholders for the implementation phase.

## Local prerequisites

The intended development environment is Docker Compose with PostgreSQL. The Compose file is a local infrastructure placeholder and does not start an application service until the implementation phase adds one.

```bash
docker compose up -d postgres
```

Cursor should implement the project incrementally according to [the roadmap](docs/roadmap.md), keeping each phase runnable and tested.
