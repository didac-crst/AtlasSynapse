# Business value

AtlasSynapse is worthwhile when an organization repeatedly pays the cost of
**reconstructing context** that should have remained structured, connected, and
attributable.

The architectural thesis is in [design-thesis.md](design-thesis.md). This note
answers a different question:

> Why should a company care — and where does this save money, reduce risk, or
> improve decisions?

## Where value shows up

| Pattern | Outcome |
| --- | --- |
| Repeated engineering investigations | Less duplicated work; reuse of prior root-cause and fix knowledge |
| “We already solved this” detection | Faster problem resolution when similar failures recur |
| Supplier-quality context | Better sourcing and risk decisions from connected quality history |
| Compliance evidence graph | Less time assembling audit trails from scattered systems |
| Change-impact analysis | Fewer missed dependencies when something moves |
| Corporate / team memory | Lower key-person dependency and onboarding cost |
| Incident memory | Lower MTTR when past incidents remain queryable with evidence |
| Decision memory | Less repeated debate and architecture archaeology |
| Cross-system customer intelligence | Better renewal and account decisions from linked signals |
| AI investigation memory | Agents accumulate understanding instead of paying context-reconstruction cost every session |

The common economic thread is **avoided rework**: fewer people and agents
re-deriving the same relationships, timelines, and evidence from CRM, ERP,
tickets, documents, and chat.

## What AtlasSynapse is for

It is for long-lived knowledge where:

- identity matters (“which supplier / person / asset?”);
- relationships matter (“what does this change touch?”);
- history and supersession matter (“what was true then vs now?”);
- provenance matters (“what evidence supports this?”);
- the domain keeps expanding beyond a fixed application schema.

It sits beside systems of record. Those systems stay authoritative for
transactions and workflows. AtlasSynapse holds the cross-cutting meaning agents
and investigators need between them.

## When AtlasSynapse is not worth it

Do **not** introduce it for:

- a simple FAQ or help-center search problem;
- generic document retrieval where passages are enough;
- stable relational workflows whose schema already covers the domain;
- one-off analysis that will not be reused;
- situations where relationships, history, and provenance do not affect
  decisions or risk.

If the pain is “find the right paragraph,” a search or RAG stack is usually
enough. AtlasSynapse becomes relevant when the pain is “decide what is true,
about what, with what evidence, across changing concepts.”

## Positioning for buyers and operators

- **Cost:** reduce repeated investigation, onboarding, and audit assembly labor.
- **Risk:** reduce silent identity mistakes, unsupported claims, and missed
  change impacts.
- **Decision quality:** keep prior conclusions, conflicts, and evidence available
  instead of relying on tribal memory.
- **AI leverage:** let agents write and retrieve structured memory under
  governance, rather than only summarizing text each time.

AtlasSynapse does not replace CRM, ERP, or ticketing. It reduces the hidden tax
of reconnecting them every time a hard question returns.
