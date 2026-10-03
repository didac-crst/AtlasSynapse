# semantic_memory package

Package boundaries follow [docs/cursor-implementation.md](../../docs/cursor-implementation.md):

- `models/` ORM models and enums
- `schemas/` Pydantic request/response/error schemas
- `repositories/` persistence operations (Phase 2+)
- `services/` semantic business logic (Phase 2+)
- `validation/` deterministic semantic validation (Phase 2+)
- `mcp/` thin typed tool adapter (placeholder in Phase 0/1)
- `api/` health and HTTP wiring
- `observability/` structured logging
- `seeding/` deterministic core ontology seed
