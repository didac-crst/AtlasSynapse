<p align="center">
  <img src="assets/logo.png" alt="AtlasSynapse logo" width="200" />
</p>

<p align="center">
  <img src="assets/wordmark.svg" alt="AtlasSynapse" width="420" />
</p>

## Your life already has a data model. It is just scattered everywhere.

Imagine using an AI assistant for the boring parts of everyday life.

You send it a receipt for a new laptop.

A few months later, you upload the warranty.

Later still, there is an email confirming that the manufacturer extended the warranty by another year.

The laptop also appears on an invoice from the shop, a bank transaction, and perhaps eventually in an insurance claim.

Each individual piece of information is easy to understand.

The useful part is everything connecting them:

```text
Laptop
 ├── purchased_from → Shop
 ├── purchased_on → 2026-02-12
 ├── purchase_price → €1,499
 ├── receipt → Receipt_123
 ├── payment → Transaction_456
 ├── warranty → Warranty_789
 └── owned_by → Person
```

Now imagine the same thing happening across years of:

- receipts;
- invoices;
- taxes;
- insurance policies;
- subscriptions;
- contracts;
- school and childcare expenses;
- household equipment;
- warranties;
- reimbursements;
- vehicles;
- travel bookings;
- administrative deadlines.

An AI agent can remember useful information about all of this.

But memory is often deliberately compact: important details are summarized into text so they remain useful within a limited context.

That is very different from preserving the underlying structure.

Eventually, questions appear such as:

> Which expenses from last year might be relevant when preparing my taxes?

> Do I still have proof of purchase for the washing machine?

> When does its warranty expire?

> Which invoice corresponds to this bank payment?

> Did I already receive the reimbursement associated with this expense?

> Which active contracts have a cancellation deadline in the next three months?

> Why do we believe this subscription costs €29.99 rather than €24.99?

At that point, remembering more text is not quite enough.

The problem has become one of **entities, relationships, time, provenance and constraints**.

That is what AtlasSynapse is for.

## What AtlasSynapse is

AtlasSynapse is a **self-hosted semantic memory layer for AI agents**.

It stores knowledge as explicit entities, relationships, events, attributes, dates, evidence and provenance rather than reducing everything to a collection of textual memories.

A conventional memory might preserve:

> The washing machine was bought in 2025 and has an extended warranty.

AtlasSynapse can preserve the structure behind that statement:

```text
WashingMachine_1 → type → Appliance

Purchase_42:
    item → WashingMachine_1
    seller → Retailer_8
    date → 2025-06-14
    amount → €649
    evidence → Receipt_192

Warranty_17:
    covers → WashingMachine_1
    provider → Manufacturer_3
    valid_from → 2025-06-14
    valid_to → 2028-06-14
    evidence → WarrantyDocument_51
```

The distinction matters.

The information can now be queried independently, connected to new information later, and traced back to its source.

## Memory is useful. Structure is what makes it powerful.

AtlasSynapse does not try to replace an agent's normal memory.

The two serve different purposes.

Textual memory is excellent for things like:

> The user prefers receiving invoices electronically.

or:

> The family usually books summer holidays several months ahead.

Those are compact pieces of context.

But some knowledge naturally has more structure:

```text
Person
    ↓ pays
Invoice
    ↓ relatesTo
ChildcareService
    ↓ occurredDuring
TaxYear
```

or:

```text
Subscription
    ├── provider
    ├── price
    ├── billing_period
    ├── started_at
    ├── cancellation_notice
    └── payment_method
```

Once that structure exists, an agent can retrieve exactly the pieces it needs instead of depending entirely on a prose summary.

## Knowledge accumulates

The interesting part is what happens over time.

Suppose AtlasSynapse initially learns:

```text
InternetContract
    monthly_price → €39.99
```

Six months later an invoice says:

```text
monthly_price → €44.99
```

AtlasSynapse does not need to erase the first fact.

It can preserve:

```text
€39.99
valid_until → 2026-05-31

€44.99
valid_from → 2026-06-01
```

with the invoice supporting each statement.

Or perhaps two documents disagree about the cancellation notice:

```text
Contract → cancellation_notice → 30 days
FAQ      → cancellation_notice → 60 days
```

Instead of silently choosing one, AtlasSynapse can preserve both claims and record that they conflict.

An agent can then inspect the sources and resolve the discrepancy.

## The same model works beyond administration

AtlasSynapse does not have a predefined `receipt` database, a `tax` database and a `warranty` database.

Those are semantic concepts.

The underlying model remains generic:

```text
Class       What kinds of things exist
Predicate   How those things can be described or connected
Entity      A concrete thing
Statement   A claim about that thing
Source      Why that claim exists
```

This means the same system can eventually represent:

```text
people
companies
projects
documents
equipment
contracts
experiments
decisions
expenses
properties
events
places
```

without creating a new SQL schema for every domain.

## An ontology that can evolve

Nobody can realistically design every concept a personal knowledge system may need in advance.

Today it may need:

```text
Receipt
Warranty
Subscription
```

Tomorrow:

```text
TaxDeductibleExpense
InsuranceClaim
CancellationWindow
```

AtlasSynapse therefore separates two concerns.

### Knowledge plane

Routine operations are deterministic and inexpensive:

- create or resolve an entity;
- assert a fact;
- connect two entities;
- attach evidence;
- record an event;
- supersede an outdated statement;
- preserve history;
- detect conflicts.

No LLM call is required simply to remember that an invoice cost €129.

### Ontology control plane

If an agent encounters a concept the ontology does not yet represent, it can propose one.

For example:

```text
CancellationWindow
```

Before adding it, AtlasSynapse can check:

1. whether it already exists;
2. whether an alias already represents it;
3. whether another concept is semantically equivalent;
4. whether the proposal is structurally valid;
5. whether it conflicts with the existing ontology;
6. optionally, whether semantic review considers it genuinely useful.

Only then does the ontology evolve.

The physical PostgreSQL schema remains stable.

**Meaning evolves as data.**

## Provenance is part of the knowledge

AtlasSynapse does not only store:

```text
Warranty expires → 2028-06-14
```

It can also preserve:

```text
source → warranty.pdf
page → 2
observed_at → ...
asserted_at → ...
```

That means an agent can answer both:

> When does the warranty expire?

and:

> Where did that information come from?

Those are very different guarantees.

## Architectural commitments

- **Python + PostgreSQL** at the core.
- SQLAlchemy, Alembic and Pydantic for persistence and contracts.
- A thin MCP layer exposing semantic operations.
- Deterministic routine knowledge writes.
- A governed ontology control plane.
- Append, supersede, retract, deprecate and merge instead of ordinary destructive mutation.
- Temporal validity as first-class data.
- Provenance and evidence as first-class data.
- Explicit ambiguity and conflict handling.
- Idempotent writes so retries remain safe.
- No arbitrary SQL or dynamic table creation through MCP.
- Optional vector similarity support; `pgvector` is not required.
- Embeddings are derived indexes, never authoritative knowledge.

## MCP surface

Agents interact with semantic operations rather than database primitives.

Examples:

```text
create_entity
assert_statement
assert_batch
supersede_statement
add_evidence
merge_entity

get_entity
search_entities
get_entity_neighborhood
get_timeline
explain_statement
find_conflicts

get_class
get_predicate
search_ontology

propose_class
propose_predicate
propose_constraint
```

There are intentionally no tools such as:

```text
run_sql
create_table
drop_table
```

The agent works with meaning.

AtlasSynapse owns storage integrity.

## Documentation

- [Architecture](docs/architecture.md)
- [Ontology model](docs/ontology-model.md)
- [Invariants](docs/invariants.md)
- [MCP contract](docs/mcp-contract.md)
- [Operations](docs/operations.md)
- [Deployment](docs/deployment.md)
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

```bash
make lint
make format
make typecheck
make ci
make seed
```

Start the HTTP API:

```bash
semantic-memory
# or full stack:
# docker compose up --build
```

MCP stdio transport:

```bash
semantic-memory-mcp
```

Health endpoints:

```text
GET /health/live
GET /health/ready
```

Non-health HTTP routes accept `Authorization: Bearer <HTTP_API_TOKEN>` or `X-API-Token` when that token is configured (required in production). See [deployment.md](docs/deployment.md).

## Project status

Phases 0–15a are implemented: the core substrate is in place (knowledge plane, provenance, conflicts, ontology governance, retrieval, LLM observability, production packaging, and agent feedback).

The next focus is **Phase 15+**: ingestion policy, administration, and advanced semantic capabilities, informed by dogfooding rather than speculative infrastructure.

Already delivered includes:

- PostgreSQL schema, migrations, ontology bootstrap, health checks, and CI;
- actors, entities, statements, temporal validity, provenance, and idempotent `operation_log` auditing;
- conflicts, batch ingestion, ontology read plane, and governed proposals with semantic review;
- database-backed retrieval with transparent ranking signals and `llm_call_log` observability;
- Docker/Compose packaging, HTTP API authentication, MCP stdio transport, and deployment docs;
- `agent_feedback` reporting (`report_feedback`) with fingerprint dedupe and admin resolve.

See the [development roadmap](docs/roadmap.md) for the full sequence.

---

**AtlasSynapse gives AI agents a structured place to put the details that are too granular, connected, temporal, or evidence-dependent to live comfortably inside ordinary conversational memory.**
