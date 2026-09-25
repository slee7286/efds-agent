# EFDS Agent architecture

Current architecture is **V2**: bounded agentic retrieval with query planning,
hybrid search, and post-generation citation verification. See
[docs/AGENT_V2_ARCHITECTURE.md](docs/AGENT_V2_ARCHITECTURE.md) for what changed
from V1 and why. This document covers runtime ownership, the security boundary,
and the request contract, which V2 preserves.

## Runtime ownership

```text
efds-site browser UI
  -> server-side Supabase session and scope check
  -> HTTPS/SSE proxy
efds-agent FastAPI
  -> verify Supabase user and active profile
  -> requested scope may only narrow access
  -> query planner (gated, cheap tier, structured output)
  -> KnowledgeRetrievalGateway
  -> fixed search_retrieval_units RPC with user JWT (hybrid multi-query)
efds-knowledge-base PostgreSQL/RLS/retrieval_units
  -> versioned permission-scoped retrieval rows (once 0014 is applied)
efds-agent ContextPackage
  -> one OpenAI Responses API call
  -> validated citations
  -> citation verification (advisory-on-failure, withholding-on-success)
efds-site citation rendering
```

`efds-knowledge-base` owns canonical source data, retrieval-unit identity,
lexical and semantic retrieval, exact rescue/fallback, authority/current-state
ranking, RLS, provenance, embedding privacy, and ContextPackage retrieval
semantics. `efds-agent` owns authentication orchestration, source-mode policy
narrowing, bounded context formatting, prompting, synthesis, abstention,
citation validation, SSE, telemetry, and agent evaluation. `efds-site` owns the
session, server proxy, UI, and citation navigation.

## Security boundary

The agent accepts only a bearer token, query, bounded conversation, requested
scope, and one of the three server-known source modes. It never accepts SQL,
table names, PostgreSQL roles, source visibility overrides, model names, or
service credentials from clients. Supabase Auth verifies the token; the active
profile determines the role. The token is forwarded unchanged to PostgREST,
so RLS remains the final authorization boundary. No service-role key or
unrestricted `DATABASE_URL` exists in the agent runtime.

There are no write tools, filesystem tools, web search, arbitrary SQL, ReAct
loop, autonomous tool loop, or multi-agent loop. The V2 query planner is not a
loop: it is one fixed-schema call whose output is a bounded list of search
queries, and its gate is deterministic code rather than a model decision.
Retrieved text is explicitly untrusted evidence and cannot alter instructions or
authorization.

## Request contract

One request performs one gated planning call, one canonical hybrid retrieval
carrying up to `SUB_QUERY_LIMIT` queries, one model synthesis call, and one
citation verification call. It creates a bounded evidence package with local IDs
`S1…Sn`, validates emitted IDs against the package, and streams `meta`, `token`,
`citations`, `done`, and safe `error` events. The final `done.answer` is
authoritative for the website: when verification removes citations or withholds
the answer, `done` carries the corrected text and flags it, and a client must
render `done.answer` rather than the concatenation of `token` events.

The number of model calls is fixed at request time, and the agent never chooses a
tool, table, role, model, or source mode from model output.

See [docs/RETRIEVAL_INTEGRATION.md](docs/RETRIEVAL_INTEGRATION.md),
[docs/AGENT_V2_ARCHITECTURE.md](docs/AGENT_V2_ARCHITECTURE.md) and
[docs/AGENT_EVALUATION.md](docs/AGENT_EVALUATION.md).
