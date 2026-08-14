# EFDS Agent V1 architecture

## Runtime ownership

```text
efds-site browser UI
  -> server-side Supabase session and scope check
  -> HTTPS/SSE proxy
efds-agent FastAPI
  -> verify Supabase user and active profile
  -> requested scope may only narrow access
  -> KnowledgeRetrievalGateway
  -> fixed search_retrieval_units PostgREST RPC with user JWT
efds-knowledge-base PostgreSQL/RLS/retrieval_units
  -> versioned permission-scoped retrieval rows (once 0014 is applied)
efds-agent ContextPackage
  -> one OpenAI Responses API call
  -> validated citations
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

There are no write tools, filesystem tools, web search, arbitrary SQL, planner,
ReAct loop, autonomous tool loop, or multi-agent loop. Retrieved text is
explicitly untrusted evidence and cannot alter instructions or authorization.

## Single-pass contract

One request performs one canonical retrieval, creates a bounded evidence package
with local IDs `S1…Sn`, performs one model synthesis call, validates emitted IDs
against the package, and streams `meta`, `token`, `citations`, `done`, and safe
`error` events. The final `done.answer` is authoritative for the website.

See [docs/RETRIEVAL_INTEGRATION.md](docs/RETRIEVAL_INTEGRATION.md) and
[docs/AGENT_EVALUATION.md](docs/AGENT_EVALUATION.md).
