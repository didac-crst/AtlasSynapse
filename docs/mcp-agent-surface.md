# MCP agent surface (`feat/mcp-agent-surface`)

## Principle

**Expose tools by agent intent, not by internal service capability.**

Tool visibility is **UX / context optimization**, not security. Capability checks,
review gates, idempotency, and audit still apply to every mutation.

## Surfaces

| Surface | Purpose | ChatGPT default |
| --- | --- | --- |
| `agent` | Normal reasoning + memory | **Yes** (`MCP_TOOL_SURFACE=agent`) |
| `advanced` | Investigation / low-level | Usually no |
| `admin` | Ops / governance extras | No |
| `all` | Full registry | Dev / Cursor |

Configured via `MCP_TOOL_SURFACE`. One MCP server; `tools/list` and dispatch are
filtered. Advanced/admin tools remain available when the surface is raised —
HTTP APIs are unchanged.

## Counts (this branch)

| Catalog | Tool count |
| --- | ---: |
| Full registry (`all`) | **41** (incl. aliases) |
| Default `agent` | **13** |

Roughly a **~70% reduction** in tools presented to ChatGPT.

## Agent catalog

```text
search_memory
get_relevant_context
get_timeline

assert_statement
correct_statement
retract_statement

answer_identity_clarification
answer_semantic_clarification

propose_class
propose_predicate
get_proposal / get_ontology_proposal
apply_ontology_proposal
```

### Why these are agent-shaped

| Tool | Intent |
| --- | --- |
| `search_memory` | “Look something up” |
| `get_relevant_context` | “What do we know about X?” |
| `get_timeline` | “How did this evolve?” |
| `assert_statement` | “Store this fact” |
| `correct_statement` | “Fix this fact” |
| `retract_statement` | “Withdraw this fact” |
| `answer_identity_clarification` | “Which Didac?” |
| `propose_*` / `apply_*` | Ontology change lifecycle |

## Hidden from agent (and why)

| Tool | Why not default |
| --- | --- |
| `supersede_statement` | Storage semantics; use `correct_statement` |
| `get_entity_neighborhood` | Folded into `get_relevant_context` |
| `search_entities` / `search_statements` | Prefer `search_memory` |
| `search_semantic_memory` | Advanced alias of `search_memory` |
| `create_entity` / `get_entity` | Covered by assert EntityInput + context |
| `get_statement` / `explain_statement` | Prefer mutation `return_mode=standard` |
| Provenance ingest/search tools | Investigation / pipeline |
| `assert_batch` / `merge_entity` | Explicit advanced ops |
| `get_runtime_config` | Diagnostics |
| `propose_constraint` / alias / parent | Less common ontology ops |
| `challenge_ontology_review` | Review edge path |
| `report_feedback` | Ops signal |

## Scenario coverage (agent-only)

```text
"Who is Didac?"                          → search_memory / get_relevant_context
"Airbus role context"                    → get_relevant_context / get_timeline
"Correct the start date"                 → correct_statement
"Store this new fact"                    → assert_statement
"This Didac might already exist"         → assert → answer_identity_clarification
"Propose a new ontology concept"         → propose_class / propose_predicate
"Apply the approved proposal"            → get_proposal → apply_ontology_proposal
```

## Deploy note

Tunnel/MCP compose should set `MCP_TOOL_SURFACE=agent` (default). Cursor/dev
can use `advanced` or `all`.
