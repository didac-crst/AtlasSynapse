# ADR-011: Python and PostgreSQL technology baseline

Status: accepted

AtlasSynapse uses Python 3.12 or newer, PostgreSQL, SQLAlchemy, Alembic, Pydantic, and FastAPI-style service boundaries. The MCP adapter and external LLM/provider integrations remain replaceable interfaces.

Python is selected for its MCP ecosystem, PostgreSQL tooling, strict schema support through Pydantic, and suitability for ontology validation, batch processing, and provider integration. PostgreSQL is the authoritative transactional store. `pgvector` remains optional and is treated as a rebuildable derived-data capability rather than a correctness dependency.
