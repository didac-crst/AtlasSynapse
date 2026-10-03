# ADR-002: Separate knowledge plane from ontology control plane

Status: accepted

Routine knowledge writes remain deterministic and low latency. Ontology mutation is governed, lower volume, auditable, and concurrency controlled. This prevents an LLM or proposal workflow from becoming a dependency of ordinary memory storage.
