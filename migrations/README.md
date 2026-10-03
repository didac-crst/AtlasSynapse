# Migrations

Alembic migrations for the AtlasSynapse PostgreSQL schema.

- `env.py` loads metadata from `semantic_memory.models`
- `versions/ca1ee463c33b_initial_schema.py` creates the v1 tables, constraints, indexes, and deterministic core ontology seed

```bash
make migrate
make migrate-down
```
