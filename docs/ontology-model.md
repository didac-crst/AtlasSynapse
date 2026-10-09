# Ontology and knowledge model

This document is the canonical v1 data contract. Cursor should implement it through SQLAlchemy models and an Alembic initial migration without inventing domain-specific tables.

## Core entities

The model uses `entity`, `entity_type`, and `entity_alias` for real-world or conceptual things. Events and rich relationships are entities typed with ontology classes. There is deliberately no physical `event` table.

## Statements

A statement has a subject entity, predicate, exactly one typed object representation, assertion time, optional validity interval, lifecycle status, and actor. Object columns are mutually exclusive at the database layer. Application validation additionally checks that the selected object representation matches the predicate's current `value_kind`.

Semantic duplicate identity is approximately:

```text
subject_entity_id + predicate_id + normalized_object + valid_from + valid_to
```

Duplicate detection is transactional service logic. A duplicate assertion reuses the statement and may add evidence.

## Temporal semantics

- `asserted_at`: when the service accepted the claim;
- `observed_at`: when the source observed or reported it;
- `valid_from`: when the claim became true;
- `valid_to`: when the claim stopped being true.

Statement lifecycle and temporal validity are orthogonal. There is no `expired` status.

## Ontology

Classes and predicates have immutable revisions and a current revision pointer. Parent links form a directed acyclic graph. Predicate revisions describe value kind, datatype, cardinality, symmetry, transitivity, inverse, and metadata. Domains and ranges refer to classes.

Ontology mutation follows proposal, gate, optimistic concurrency, immutable revision, and change-record semantics — with a stricter lifecycle than “propose then write”:

1. **Propose** — agents create proposals (`ontology.propose`); this does not mutate ontology tables.
2. **Deterministic gates** — structural validity, keys/aliases, cycles, domain/range, revision concurrency.
3. **Semantic reuse / overlap review** — optional LLM judgment; may recommend reuse instead of creating a near-duplicate concept.
4. **Clarification / challenge** — when meaning is ambiguous, AtlasSynapse issues a clarification ID; proposers may challenge negative semantic decisions with new reasoning.
5. **`READY_TO_APPLY`** — eligible for an apply *attempt*, not a committed mutation.
6. **Authorized apply** — requires `ontology.apply`; the server **revalidates** gates and live ontology state at commit time. There is no force/skip path.

Proposal ≠ mutation. Agents may discover that the semantic model is insufficient; they cannot silently redefine it.

## Provenance

Sources are reusable records. Evidence links statements to sources with optional excerpts, locators, and extraction confidence. Unknown source reliability remains null; it is not treated as perfect reliability.

## Bootstrap ontology

Namespace `core` contains:

```text
Thing, Agent, Person, Organization, Place, Event, Activity,
Project, Document, Observation, Decision, RelationshipContext, Claim
```

Initial inheritance:

```text
Agent -> Thing
Place -> Thing
Person -> Agent
Organization -> Agent
Event -> Thing
Activity -> Thing
Project -> Activity
Document -> Thing
Observation -> Event
Decision -> Event
RelationshipContext -> Thing
Claim -> Thing
```

Initial predicates:

```text
name, description, actor, hasParticipant, occurredAt,
startedAt, endedAt, locatedAt, relatedTo, source,
authoredBy, publicationContext,
makesClaim, claimText, claimSubject, claimPredicateKey,
claimObject, claimObjectString, epistemicKind, claimPolarity,
claimStatus, claimDerivation, aboutEntity
```

`Claim` is a source-scoped speech-act occurrence (`Document --makesClaim--> Claim`).
Claim interiors (`claimText`, `claimSubject`, `claimPredicateKey`, …) are **not**
world beliefs. Default retrieval excludes Claims unless `include_claims=true`
(see [knowledge-ingestion-v1.md](knowledge-ingestion-v1.md) Phase A).

This seed stays intentionally small beyond the Claim epistemic bootstrap.
User or domain concepts belong in governed proposals.
