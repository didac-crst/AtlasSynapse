# Design thesis

AtlasSynapse is a governed semantic-memory layer for AI agents.

The short form of the thesis:

> AI agents need persistent structured knowledge across domains, but the concepts
> they will encounter cannot all be designed upfront. AtlasSynapse separates a
> stable knowledge substrate from an evolving semantic model, while governing
> identity, evidence, ambiguity, and changes to that model.

The differentiator underneath it:

> The agent can discover that its current semantic model is insufficient, but it
> cannot silently redefine that model.

## Why fixed schemas are insufficient

Most software assumes its domain can be modeled in advance.

A CRM knows customers, contacts, and opportunities. A ticketing system knows
issues and workflows. An ERP knows products, orders, and transactions.

That works when the domain and the questions are known.

AI agents make this problem more acute. They encounter and attempt to
operationalize changing knowledge autonomously and at higher frequency — people,
projects, failures, decisions, contracts, experiments, suppliers, requirements,
locations, evidence, commitments, and relationships that nobody anticipated when
the database was designed.

Continuously extending application schemas for every newly discovered semantic
concept becomes increasingly costly when the domain is open-ended and
cross-cutting. It couples storage migrations to semantic discovery and forces
every agent to wait on schema ownership.

## Why raw text memory is insufficient

Text preserves nuance well. It is a poor substrate for:

- identity (“which Didac?”);
- temporal truth (“what was true then vs now?”);
- evidence (“where did that claim come from?”);
- conflict (“two sources disagree”);
- reuse across domains (“the same Person in warranty, employment, and incident”).

A vector index over text can retrieve similar passages. It does not decide whether
two mentions are the same entity, whether a fact was superseded, or whether a new
label is a synonym of an existing predicate.

AtlasSynapse does not replace conversational memory. It makes selected meaning
explicit so agents can query, connect, challenge, and evolve it.

## Stable physical schema, evolving semantic model

AtlasSynapse keeps the **physical storage model** stable while allowing the
**semantic model** to grow.

Entities, relationships, and facts live on a generic knowledge substrate
(PostgreSQL tables for entities, statements, provenance, ontology records).

New domain meaning is normally introduced as **ontology data** — classes,
predicates, constraints, aliases — not as new application tables.

> Keep the physical storage model stable while allowing the semantic model to grow.

Ordinary semantic expansion should create new data, not new database structures.

## Complements systems of record

Source systems remain authoritative for operational data. AtlasSynapse preserves
the meaning that connects information across them:

```mermaid
%%{init: {"theme":"base","themeVariables":{"fontFamily":"ui-sans-serif, system-ui, sans-serif","fontSize":"14px","lineColor":"#64748B"}}}%%
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

This architecture can be applied to engineering defect memory, supplier-quality
intelligence, audit evidence graphs, change-impact analysis, organizational
handovers, and personal administration. The shared requirement is long-lived,
connected knowledge whose concepts cannot all be known upfront.

## Server-owned identity

Agents should not each invent their own fragile resolve-then-assert sequence.
That creates races and inconsistent identity policies.

AtlasSynapse owns identity on the write path. An agent may submit unresolved
entity inputs; the service decides MATCH, CREATE, or CLARIFY independently for
each side. Weak evidence does not silently collapse two identities.

## Uncertainty becomes clarification

When identity or ontology meaning cannot be resolved safely, AtlasSynapse does
not guess.

It creates a durable **control-plane** clarification handle: frozen operation,
candidates, expiry, one-shot resolution. Answering that handle rechecks current
production state before continuing. If the world changed while waiting, AtlasSynapse
clarifies again rather than committing against stale assumptions.

Clarification state is operational control-plane state. It is not knowledge and
not provenance.

## Ontology proposal is not ontology mutation

Agents may propose how the semantic model should evolve. AtlasSynapse still owns:

- deterministic structural gates;
- semantic reuse / overlap review;
- clarification and challenge;
- authorization (`ontology.apply`);
- revalidation against live ontology state at apply time.

`READY_TO_APPLY` means eligible for an apply attempt, not a committed mutation.
There is no force/skip path around gates.

## Two feedback loops

### Knowledge accumulation

```mermaid
%%{init: {"theme":"base","themeVariables":{"fontFamily":"ui-sans-serif, system-ui, sans-serif","fontSize":"14px","lineColor":"#2A8F85"}}}%%
flowchart TD
  info[Information] --> id[Identity resolution]
  id --> facts[Entities + statements]
  facts --> ev[Evidence + temporal state]
  ev --> ret[Retrieval]
  ret --> reason[Reasoning]

  classDef step fill:#E8F7F5,stroke:#2A8F85,color:#0F3F3B,stroke-width:1.5px
  class info,id,facts,ev,ret,reason step
```

### Semantic-model evolution

```mermaid
%%{init: {"theme":"base","themeVariables":{"fontFamily":"ui-sans-serif, system-ui, sans-serif","fontSize":"14px","lineColor":"#5B6B7C"}}}%%
flowchart TD
  nk[New knowledge] --> enough{Ontology sufficient?}
  enough -->|yes| reuse[Reuse existing semantics]
  enough -->|no| propose[Propose ontology change]
  propose --> gates[Deterministic validation]
  gates --> review[Semantic reuse / overlap review]
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

An agent should not create a new concept merely because it cannot find one, and
it should not silently force unfamiliar knowledge into an incorrect existing
category. Semantic-model gaps are governed decisions.

> AtlasSynapse governs not only what an agent knows, but how the language it uses
> to represent knowledge is allowed to evolve.

## Related documents

- [Business value](business-value.md)
- [Architecture](architecture.md)
- [Ontology model](ontology-model.md)
- [MCP contract](mcp-contract.md)
- [Invariants](invariants.md)
