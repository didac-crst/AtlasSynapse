# ADR-006: Deterministic validation precedes semantic review

Status: accepted

Semantic review may recommend accept, reject, reuse, or manual review. It cannot bypass schema, authorization, structure, cycles, domain/range, concurrency, or final deterministic validation, and it cannot mutate the database directly.
