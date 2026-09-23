# EFDS Agent V1 retrieval integration

## Boundary

```text
authenticated efds-site request
    -> efds-agent auth and requested-scope narrowing
    -> one allowlisted Supabase PostgREST RPC
    -> permission-scoped canonical retrieval rows
    -> bounded ContextPackage
    -> one OpenAI Responses API synthesis call
    -> validated S1/S2 citations
    -> SSE back through efds-site
```

`efds-knowledge-base` owns `retrieval_units`, ingestion, embeddings, semantic
retrieval, lexical rescue/fallback, authority/current-state ranking, RLS,
provenance, and the `ContextPackage` retrieval semantics. The agent calls the
fixed `search_retrieval_units_v1` SECURITY INVOKER RPC for ordinary QA and
`committee_ticket_slack_evidence_v1` for committee ticket drafts. The latter is
a backend-owned SECURITY DEFINER function with explicit actor, public-channel
and current-message checks. The agent does not import the
knowledge-base package, copy its SQL, search source tables, compute ranking, or
use a service-role/database connection.

The backend migration `0014_agent_retrieval_contract` exposes the fixed
`search_retrieval_units_v1` wrapper. The migration currently delegates to the
lexical `search_retrieval_units` SQL function. This is an important live
validation finding: the frozen benchmark's `semantic_primary_exact` path is
`query -> KB embedding provider -> search_retrieval_units_semantic -> exact
rescue`, whereas v1 currently accepts only text and cannot create that query
embedding inside PostgreSQL. Therefore the agent must not claim benchmark
parity until the KB owner exposes a canonical RPC/service path that performs
the same semantic-primary behavior. The agent fails closed on this mismatch;
it does not compute embeddings, rank rows, or fall back to legacy adapters.

The ordinary QA PostgREST contract is versioned in the agent as contract `1` and uses
fixed arguments: `search_query`, `requested_source_types`, the allowlisted
optional filters (currently null), `include_history=false`, and bounded
`result_limit`/`result_offset`. The caller's `Authorization: Bearer
<Supabase JWT>` is forwarded to PostgREST with the public anon/publishable key.
The JWT keeps database RLS user-scoped; the key identifies the project and is
not an authorization escalation. `scripts/rpc_health.py` checks the function
without applying migrations.

## Modes and bounds

`preterm_knowledge` is the default beta mode. It allows ICU/structured
knowledge, approved `01_governance` documents, and operational source types if
they become populated. The mode narrows access after the RLS-scoped RPC and
never expands it. `full_institutional` is structurally defined for ICU,
structured knowledge, documents, Slack, meeting notes, and operational sources,
but is not certified. `committee_tickets` requires committee or admin scope and
accepts only committee-visible current Slack rows from the dedicated RPC;
it does not expose private channels or meeting notes. `public` is a lower policy mode and remains subject to
anonymous RLS/public visibility.

The server default is `AGENT_RETRIEVAL_K=10`, with a hard cap of
`AGENT_MAX_RETRIEVAL_K=20`. The RPC candidate window is bounded at 50 to allow
mode narrowing without a second retrieval. The agent preserves backend order,
deduplicates stable retrieval-unit IDs, and applies only the context item and
character budget. It never re-ranks rows.

Each selected row becomes a local model citation ID (`S1`, `S2`, …) mapped to
`retrieval_unit_id`, source record/version, provenance, authority, currentness,
and an authorized navigation route. The IDs are per response and are never
accepted from the browser. Model citations are removed unless they exist in the
current package; invented URLs and UUIDs are never displayed.

## Failure behavior

Zero authorized evidence produces a deterministic insufficient-evidence answer
and skips OpenAI. A one-result package is marked `limited` and the model is
instructed to qualify claims. Retrieval dependency failure is a controlled
503/non-success SSE event; it is not silently turned into a model-only answer.
The latest pre-term benchmark Recall@10 remains 0.7059, so abstention is an
intentional product behavior, not an exceptional path.

## Deprecation

The former ICU, Slack, document, operational, and intent-router modules remain
only as deprecated compatibility code for existing unit tests/CLI utilities.
They are not imported by `AgentOrchestrator` and cannot participate in primary
QA. New retrieval work belongs in `efds-knowledge-base` and its fixed RPC
contract.
