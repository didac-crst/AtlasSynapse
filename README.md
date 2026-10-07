<p align="center">
  <img src="assets/logo.png" alt="AtlasSynapse logo" width="200" />
</p>

<p align="center">
  <img src="assets/wordmark.svg" alt="AtlasSynapse" width="420" />
</p>

<br>

<h2 align="center">
AI can remember text.<br>
AtlasSynapse gives it a world model.
</h2>

<p align="center">
<strong>An open-source reference implementation and working demonstrator of governed semantic memory for AI agents.</strong>
</p>

<p align="center">
  <a href="https://github.com/didac-crst/atlas-synapse/actions/workflows/ci.yml"><img src="https://github.com/didac-crst/atlas-synapse/actions/workflows/ci.yml/badge.svg" alt="CI" /></a>
  <img src="https://img.shields.io/badge/python-3.12%2B-3776AB?logo=python&logoColor=white" alt="Python 3.12+" />
  <img src="https://img.shields.io/badge/PostgreSQL-16-4169E1?logo=postgresql&logoColor=white" alt="PostgreSQL" />
  <img src="https://img.shields.io/badge/MCP-stdio-0F766E" alt="MCP" />
  <img src="https://img.shields.io/badge/Docker-compose-2496ED?logo=docker&logoColor=white" alt="Docker" />
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-Apache%202.0-blue.svg" alt="Apache 2.0" /></a>
</p>

<br>

> **Project status:** AtlasSynapse is an actively developed reference implementation
> and working demonstrator, not a production-certified enterprise platform. It is
> currently dogfooded through ChatGPT over MCP and a self-hosted PostgreSQL
> backend. ChatGPT is one agent client; AtlasSynapse itself is independent and
> exposes MCP/API interfaces.

```mermaid
%%{init: {"theme":"base","themeVariables":{"fontFamily":"ui-sans-serif, system-ui, sans-serif","fontSize":"14px","lineColor":"#64748B"}}}%%
flowchart TB
  agent[ChatGPT / other agent] -->|MCP| atlas((AtlasSynapse))
  atlas --> pg[(PostgreSQL semantic memory)]

  classDef client fill:#EAF2FB,stroke:#3B6EA5,color:#163A5F,stroke-width:1.5px
  classDef hub fill:#E8F7F5,stroke:#2A8F85,color:#0F3F3B,stroke-width:2px
  classDef store fill:#F8FAFC,stroke:#94A3B8,color:#334155,stroke-width:1.5px
  class agent client
  class atlas hub
  class pg store
```

---

## Memory is not the same as knowledge

An assistant can remember:

> The laptop was bought in 2026 and has an extended warranty.

Useful. Eventually you also need:

> Where was it bought? Which receipt proves it? When does the warranty expire?
> Which email changed that date? Which source should we trust?

Those questions are about **entities, relationships, time and evidence** — not
about remembering more text.

AtlasSynapse makes that structure explicit:

```mermaid
%%{init: {"theme":"base","themeVariables":{"fontFamily":"ui-sans-serif, system-ui, sans-serif","fontSize":"14px","primaryColor":"#E8F7F5","primaryTextColor":"#0F3F3B","primaryBorderColor":"#2A8F85","lineColor":"#5B6B7C","secondaryColor":"#EEF2F6","tertiaryColor":"#F7F8FA","background":"#FFFFFF"}}}%%
flowchart LR
  Laptop((Laptop))
  Shop[Shop]
  Date["2026-02-12"]
  Receipt[Receipt_123]
  Warranty[Warranty_789]
  Person[Person]

  Laptop -->|purchased_from| Shop
  Laptop -->|purchased_on| Date
  Laptop -->|receipt| Receipt
  Laptop -->|warranty| Warranty
  Laptop -->|owned_by| Person

  classDef hub fill:#E8F7F5,stroke:#2A8F85,color:#0F3F3B,stroke-width:2px
  classDef node fill:#F7F8FA,stroke:#94A3B8,color:#1F2933,stroke-width:1.5px
  class Laptop hub
  class Shop,Date,Receipt,Warranty,Person node
```

Instead of preserving only what was said, AtlasSynapse preserves what exists, how
things relate, when facts were true, and where those facts came from.

## What AtlasSynapse is

AtlasSynapse is an **open-source reference implementation and working demonstrator**
for governed semantic memory in AI-agent systems.

It explores how agents can accumulate structured knowledge, resolve identity,
preserve provenance and history, and participate in semantic-model evolution under
deterministic and LLM-assisted governance — without silently redefining meaning.

Plain text remains excellent for nuance and conversational context. AtlasSynapse
does not replace that memory. It makes selected structure explicit so an agent
can query it, connect it, challenge it, and evolve it under governance.

> **Text preserves meaning. Ontology makes selected meaning explicit.**

## Why AtlasSynapse matters

Most software assumes its domain can be modeled in advance.

A CRM knows customers and opportunities. A ticketing system knows issues. An ERP
knows products and orders. That works when the domain and the questions are known.

AI agents make this problem more acute. They encounter people, projects,
failures, decisions, contracts, experiments, suppliers, requirements, locations,
evidence, and relationships autonomously and at higher frequency — often beyond
what anyone anticipated when the database was designed.

One option is to keep all of that as text. Another is to continuously extend
application schemas. AtlasSynapse takes a third approach:

> **Keep the physical storage model stable while allowing the semantic model to grow.**

Entities, relationships, and facts live on a generic knowledge substrate. New
domain meaning is normally introduced as ontology data, not as new tables.

```mermaid
%%{init: {"theme":"base","themeVariables":{"fontFamily":"ui-sans-serif, system-ui, sans-serif","fontSize":"14px","primaryColor":"#E8F7F5","primaryTextColor":"#0F3F3B","primaryBorderColor":"#2A8F85","lineColor":"#64748B","secondaryColor":"#EEF2F6","tertiaryColor":"#F7F8FA"}}}%%
flowchart LR
  subgraph sources [Systems of record]
    CRM[CRM]
    ERP[ERP]
    Jira[Jira]
    Docs[Documents]
    Email[Email]
  end

  Atlas((AtlasSynapse))
  Agents[AI agents]

  CRM --> Atlas
  ERP --> Atlas
  Jira --> Atlas
  Docs --> Atlas
  Email --> Atlas
  Atlas --> Agents

  classDef hub fill:#E8F7F5,stroke:#2A8F85,color:#0F3F3B,stroke-width:2px
  classDef src fill:#F8FAFC,stroke:#94A3B8,color:#334155,stroke-width:1.5px
  classDef agent fill:#EAF2FB,stroke:#3B6EA5,color:#163A5F,stroke-width:1.5px
  class Atlas hub
  class CRM,ERP,Jira,Docs,Email src
  class Agents agent
```

Source systems remain authoritative for operational data. AtlasSynapse preserves
the meaning that connects information across them.

This architecture can be applied to engineering defect memory, supplier-quality
intelligence, audit evidence graphs, change-impact analysis, organizational
handovers, operational incident memory, and personal administration. The shared
requirement is **long-lived, connected knowledge whose concepts cannot all be
known upfront**.

For the architectural argument, see [Design thesis](docs/design-thesis.md).
For outcomes and when *not* to use it, see [Business value](docs/business-value.md).

## Knowledge writes own identity resolution

An agent should not need a fragile client-side sequence such as:

```text
search for Didac → guess the entity → maybe create one → assert with its ID
```

That creates races and makes every agent invent its own identity policy.

AtlasSynapse owns identity on the write path. An agent can submit:

```text
Didac → employedBy → Airbus
```

using unresolved entity inputs. AtlasSynapse decides independently whether each
side should **MATCH**, **CREATE**, or **CLARIFY**. Supporting names and weak
graph evidence do not silently collapse two identities.

### Clarification is resumable

When identity cannot be resolved safely, the operation does not simply fail.

AtlasSynapse creates a durable control-plane clarification request containing the
frozen operation and candidate identities. The agent or user can later choose an
existing entity, create a new one, or reject the operation.

Before continuing, AtlasSynapse rechecks current production state. If the world
changed while waiting, it clarifies again rather than committing against stale
assumptions. Handles are one-shot and expire.

Clarification state is operational control-plane state — not knowledge and not
provenance.

### Preview the real decision path

Every mutation supports `dry_run=true`.

A dry run is not a separate approximation. AtlasSynapse executes the same
identity, validation, and semantic-review logic inside a database savepoint and
rolls the knowledge mutation back.

An agent can ask what AtlasSynapse would do without creating knowledge merely to
discover the answer. A clarification originating from a dry run can never be
turned into a real write by answering it; persistence always requires an explicit
execute request.

## Knowledge accumulates

Suppose AtlasSynapse learns a monthly price, then an invoice revises it six months
later. The first fact need not be erased: both claims can keep temporal bounds and
supporting evidence.

When two sources disagree, AtlasSynapse can preserve both claims and record a
conflict rather than silently choosing one. An agent can inspect the sources and
resolve the discrepancy.

## Stable physical schema, evolving meaning

A conventional application hard-codes domain concepts into tables and columns.
That is appropriate when the domain is known and stable.

AtlasSynapse is designed for a knowledge model that keeps evolving — including
concepts that were not anticipated when the database was designed.

New classes, predicates, entities, and statements are normally added as **data**,
not by creating new SQL tables. Architecturally:

1. **Stable physical schema** — PostgreSQL tables for entities, statements,
   provenance, and ontology records;
2. **Evolving semantic model** — classes, predicates, and constraints that
   describe what the knowledge means.

> **New meaning should usually create new data, not new database structures.**

## An ontology that can evolve

Routine knowledge writes stay on the knowledge plane: create or resolve entities,
assert facts, attach evidence, supersede outdated statements, detect conflicts —
deterministically and inexpensively.

If an agent encounters a concept the ontology does not yet represent, it proposes
one. Ontology changes pass through quality gates:

1. **Deterministic gates** — existence, aliases, structure, cycles, domain/range;
2. **Semantic review** — optional LLM judgment of meaning, equivalence, and fit;
3. **Clarification / challenge** — when meaning is ambiguous or a negative decision
   can be overturned with new reasoning;
4. **Authorized apply** — only then may an approved proposal mutate the ontology.

`READY_TO_APPLY` means eligible for an apply attempt, not a committed mutation.
Apply revalidates live ontology state. There is no force/skip path.

### The knowledge model can learn too

There are two feedback loops.

The first accumulates knowledge:

```mermaid
%%{init: {"theme":"base","themeVariables":{"fontFamily":"ui-sans-serif, system-ui, sans-serif","fontSize":"14px","lineColor":"#2A8F85"}}}%%
flowchart LR
  info[Information] --> id[Identity]
  id --> facts[Entities + statements]
  facts --> ev[Evidence + time]
  ev --> ret[Retrieval]
  ret --> reason[Reasoning]

  classDef step fill:#E8F7F5,stroke:#2A8F85,color:#0F3F3B,stroke-width:1.5px
  class info,id,facts,ev,ret,reason step
```

The second appears when new knowledge cannot be represented correctly:

```mermaid
%%{init: {"theme":"base","themeVariables":{"fontFamily":"ui-sans-serif, system-ui, sans-serif","fontSize":"14px","lineColor":"#5B6B7C"}}}%%
flowchart TD
  nk[New knowledge] --> enough{Ontology sufficient?}
  enough -->|yes| reuse[Reuse existing semantics]
  enough -->|no| propose[Propose change]
  propose --> gates[Deterministic gates]
  gates --> review[Semantic review]
  review -->|reuse| reuseExisting[Reuse recommended]
  review -->|reject| rejectSem[Reject]
  review -->|clarify| clarify[Clarification]
  clarify --> rereview[Re-review]
  rereview --> review
  review -->|ready| apply[Authorized apply]

  classDef start fill:#E8F7F5,stroke:#2A8F85,color:#0F3F3B,stroke-width:2px
  classDef process fill:#EEF2F6,stroke:#5B6B7C,color:#1F2933,stroke-width:1.5px
  classDef decision fill:#F7F8FA,stroke:#94A3B8,color:#1F2933,stroke-width:1.5px
  classDef ok fill:#E8F7EF,stroke:#2F8F5B,color:#145C32,stroke-width:1.5px
  classDef reject fill:#FDECEC,stroke:#C23B3B,color:#5C1414,stroke-width:1.5px
  classDef clarifyNode fill:#EAF2FB,stroke:#3B6EA5,color:#163A5F,stroke-width:1.5px
  class nk start
  class enough decision
  class reuse,reuseExisting,apply ok
  class propose,gates,review,rereview process
  class rejectSem reject
  class clarify clarifyNode
```

An agent should not invent a concept merely because it cannot find one, and it
should not force unfamiliar knowledge into an incorrect existing category.

> **AtlasSynapse governs not only what an agent knows, but how the language it uses to represent knowledge is allowed to evolve.**

```mermaid
%%{init: {"theme":"base","themeVariables":{"fontFamily":"ui-sans-serif, system-ui, sans-serif","fontSize":"14px","lineColor":"#5B6B7C","primaryColor":"#E8F7F5","primaryTextColor":"#0F3F3B","primaryBorderColor":"#2A8F85"}}}%%
flowchart TD
  proposal([Proposal]) --> gates[Deterministic quality gates]
  gates -->|hard failure| rejectInvalid([Reject invalid])
  gates -->|structurally valid| semantic[Semantic review]

  semantic --> approve([Approve])
  semantic --> rejectReuse([Reject reuse])
  semantic --> clarity[Needs clarity]

  clarity --> ask[Clarification request]
  ask --> answer[Proposer answers]
  answer --> rereview[Re-review]
  rereview --> approve
  rereview --> rejectReuse
  rereview --> clarity

  classDef start fill:#E8F7F5,stroke:#2A8F85,color:#0F3F3B,stroke-width:2px
  classDef process fill:#EEF2F6,stroke:#5B6B7C,color:#1F2933,stroke-width:1.5px
  classDef reject fill:#FDECEC,stroke:#C23B3B,color:#5C1414,stroke-width:2px
  classDef approveNode fill:#E8F7EF,stroke:#2F8F5B,color:#145C32,stroke-width:2px
  classDef clarify fill:#EAF2FB,stroke:#3B6EA5,color:#163A5F,stroke-width:1.5px

  class proposal start
  class gates,semantic,rereview process
  class rejectInvalid,rejectReuse reject
  class approve approveNode
  class clarity,ask,answer clarify
```

## Provenance is part of the knowledge

AtlasSynapse does not only store a warranty expiry date. It can also preserve the
source document, locator, and observation/assertion times — so an agent can answer
both *when* and *why we believe it*.

## Retrieval

Hybrid retrieval v1 combines tokenized lexical matching, temporal/effective-state
ranking, soft predicate intent, and bounded graph anchors. A frozen project gold
set is used for regression testing; the current benchmark reaches **100% Recall@3**
and **~0.99 MRR on that set**.

See [retrieval-hybrid-v1.md](docs/retrieval-hybrid-v1.md).

## Architectural commitments

- **Python + PostgreSQL** at the core (SQLAlchemy, Alembic, Pydantic).
- Thin MCP layer exposing semantic operations (intent-shaped agent surfaces).
- Deterministic routine knowledge writes; governed ontology control plane.
- Server-owned identity, resumable clarification, full-path dry-run.
- Append, supersede, retract, deprecate, and merge — not ordinary destructive mutation.
- Temporal validity, provenance, and evidence as first-class data.
- Explicit ambiguity and conflict handling; idempotent writes.
- No arbitrary SQL or dynamic table creation through MCP.
- Optional vector similarity; embeddings are derived indexes, never authoritative truth.

## MCP surface

Agents interact with semantic operations, not database primitives. The default
ChatGPT catalog (`MCP_TOOL_SURFACE=agent`) is a **12-tool** intent-shaped surface;
`advanced` / `admin` / `all` expand visibility. Surfaces are UX, not authorization.

Grouped by intent (full registry may include more; see the MCP contract):

```mermaid
%%{init: {"theme":"base","themeVariables":{"fontFamily":"ui-sans-serif, system-ui, sans-serif","fontSize":"13px","lineColor":"#94A3B8"}}}%%
flowchart TB
  MCP((MCP surface))

  MCP --> Know[Knowledge]
  MCP --> Ctx[Context]
  MCP --> Ev[Evidence]
  MCP --> Onto[Ontology]
  MCP --> Clar[Clarification]

  Know --- K1["assert / correct / retract · create · batch · …"]
  Ctx --- C1["search_memory · relevant_context · timeline · …"]
  Ev --- E1["ensure_source · ingest · add_evidence"]
  Onto --- O1["propose_* · get_ontology_proposal · apply · …"]
  Clar --- X1["answer_identity / answer_semantic clarification"]

  classDef hub fill:#E8F7F5,stroke:#2A8F85,color:#0F3F3B,stroke-width:2px
  classDef group fill:#EEF2F6,stroke:#5B6B7C,color:#1F2933,stroke-width:1.5px
  classDef detail fill:#F8FAFC,stroke:#CBD5E1,color:#64748B,stroke-width:1px
  class MCP hub
  class Know,Ctx,Ev,Onto,Clar group
  class K1,C1,E1,O1,X1 detail
```

There are intentionally no tools such as `run_sql`, `create_table`, or `drop_table`.

Authoritative catalogs: [MCP contract](docs/mcp-contract.md) and
[MCP agent surface](docs/mcp-agent-surface.md).

## Documentation

- [Design thesis](docs/design-thesis.md)
- [Business value](docs/business-value.md)
- [Architecture](docs/architecture.md)
- [Ontology model](docs/ontology-model.md)
- [Data model](docs/data-model.md)
- [Invariants](docs/invariants.md)
- [MCP contract](docs/mcp-contract.md)
- [MCP agent surface](docs/mcp-agent-surface.md)
- [Hybrid retrieval v1](docs/retrieval-hybrid-v1.md)
- [Operations](docs/operations.md)
- [Deployment](docs/deployment.md)
- [Error catalog](docs/error-catalog.md)
- [Development roadmap](docs/roadmap.md)

## Local development

```bash
python -m venv .venv
source .venv/bin/activate

make install
make infra-up
make migrate
make test
```

Useful targets: `make lint`, `make format`, `make typecheck`, `make ci`, `make seed`.

```bash
semantic-memory          # HTTP API
semantic-memory-mcp      # MCP stdio
# docker compose up --build
```

Health: `GET /health/live`, `GET /health/ready`.

Non-health HTTP routes accept `Authorization: Bearer <HTTP_API_TOKEN>` or
`X-API-Token` when configured (required in production). See
[deployment.md](docs/deployment.md).

## Project status

AtlasSynapse is actively developed and already supports the complete core
knowledge lifecycle: identity-aware writes, temporal statements, provenance,
conflicts, governed ontology evolution, semantic clarification, retrieval,
dry-run mutation previews, MCP access, operational inspection, and deployment
packaging.

Recent capability milestones (after the original phase roadmap):

- identity-resolution layer with deterministic evidence and bounded LLM adjudication (#11);
- server-owned identity on statement writes; resumable write clarification;
- full-path dry-run mutation previews;
- explicit correction / supersession / retraction envelopes;
- governed ontology apply from MCP; selectable agent MCP surfaces;
- concurrent MCP dispatch and latency diagnosis;
- hybrid retrieval v1 and frozen retrieval regression benchmark (#15).

See the [development roadmap](docs/roadmap.md) for history and next focus.

---

**AtlasSynapse is a reference implementation exploring how AI agents can keep structured, temporal, evidence-backed knowledge — and evolve the semantic model used to represent it — under governance rather than guesswork.**

Licensed under the [Apache License 2.0](LICENSE).
