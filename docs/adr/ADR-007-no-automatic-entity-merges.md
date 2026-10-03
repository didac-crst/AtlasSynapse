# ADR-007: Entity merges are explicit

Status: accepted

Candidate matches produce reuse or ambiguity based on deterministic evidence. Potential duplicates are never silently merged. A separate explicit merge operation preserves the source row and redirects future resolution.
