# Tests

Phase 0/1 coverage includes:

- fresh-database migration from zero
- critical database constraints
- deterministic core ontology seed
- `/health/live` and `/health/ready`

```bash
make test
```

Tests expect PostgreSQL available at `DATABASE_URL` (see `.env.example`).
