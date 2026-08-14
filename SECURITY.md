# Security notes

The agent is a new security boundary, not a trusted database administrator.

## Required invariants

1. HTTP authorization begins with a Supabase-verified bearer token or public unauthenticated mode.
2. The access role comes from the authenticated `profiles` row, never from JSON, query parameters, local storage, or headers.
3. Every database read uses the user's token through PostgREST so existing RLS policies remain authoritative.
4. `SUPABASE_SERVICE_ROLE_KEY`, direct `DATABASE_URL`, model-issued SQL, and write tools are forbidden in ordinary retrieval.
5. Retrieved Slack/document/article text is untrusted content and cannot override the system prompt.
6. Citations are generated from evidence and unknown IDs are removed after generation.

Production review should additionally restrict network ingress to the website/server callers, rotate keys through the platform secret store, configure CORS narrowly, add rate limits at the edge, and monitor 401/403/source-error rates without logging bearer tokens or private source bodies.

The current backend RLS model makes Slack and OneDrive filesystem archives admin-only. If that changes, update the backend policies and source-mode policy together; do not broaden access by changing only a prompt or Python enum.

## V1 web-path review

- The website validates the Supabase user server-side and forwards the short-lived access token to the agent. A requested scope may reduce access but cannot elevate the profile role.
- `EFDS_AGENT_SHARED_SECRET`, when configured, authenticates the website service caller only. It is not a role, does not replace the bearer token, and is never sent to the browser.
- The website retries an upstream agent 401 once after Supabase SSR refresh. Refresh failure becomes a clean sign-in response; there is no retry loop.
- Production website requests fail closed if `EFDS_AGENT_URL` is missing. The local mock fallback is development-only.
- The agent skips model invocation for zero authorized evidence. This prevents OpenAI from answering public questions from general memory when the public corpus is empty.
- Agent SSE errors expose generic messages only. Tokens, refresh tokens, API keys, database credentials, private source bodies and hidden reasoning are not placed in trace output.
- The production design uses the website server as the network boundary; the browser does not need direct agent CORS access.
- The primary agent path does not build PostgREST filters or planner terms. It sends only the fixed retrieval RPC payload and a server-owned source-type policy.

## Retrieval validation findings

- The inspected live database is reachable through the backend’s configured PostgreSQL connection, but the local Supabase bearer token is expired and receives HTTP 403 from Supabase Auth. Authenticated PostgREST/RLS retrieval therefore remains not verified until a fresh user token is supplied.
- The configured public resource view returns zero rows, so public queries correctly return no evidence rather than falling back to internal ICU tables.
- The backend source contains migrations `0010_unified_retrieval`, `0013_semantic_retrieval`, and the new `0014_agent_retrieval_contract`; the agent does not apply migrations automatically. The v1 RPC must be applied through the knowledge-base owner before live agent retrieval is considered ready.
- The agent consumes canonical retrieval snippets and authority/current-state metadata; it does not fall back to source-table adapters or present proposed extraction as approved truth.
- Document evidence and citation paths redact absolute Windows paths. Slack retrieval is only planned/queryable at scopes permitted by the backend model; member discussion queries do not fall back to unrelated internal knowledge.

## Current V1 verification status

The public no-LLM PostgREST smoke test is verified. Authenticated role/RLS matrix tests, internal website-to-agent E2E, and live OpenAI over internal evidence remain NOT VERIFIED until a fresh Supabase session and OpenAI key are supplied. The safe next step is to sign in through the development website and request `http://localhost:4587/api/dev/access-token`; keep the returned value only in the current PowerShell process and remove it afterwards.
