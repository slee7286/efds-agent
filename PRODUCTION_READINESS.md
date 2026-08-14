# EFDS Agent V1 production readiness

## Architecture

```text
browser
  ↓ HTTPS
imperial-efds.com /api/chat
  ↓ server-side fetch, optional service-auth secret
hosted efds-agent
  ↓ Supabase user JWT + publishable key
Supabase Auth → PostgREST → existing RLS
  ↓ bounded evidence
OpenAI Responses API (server-side API key)
  ↓ SSE: meta, token, citations, done
browser chat UI
```

The browser never calls the agent directly, receives an OpenAI key, receives a database credential, or supplies a trusted role. The website may request a lower scope; the agent resolves the effective scope from the verified Supabase identity and profile.

## Status checklist

### READY — implemented and locally verified

- Read-only typed retrieval; no write tools or arbitrary SQL.
- Supabase identity verification and RLS-preserving user-token PostgREST.
- Scope escalation rejection and prompt-independent authorization tests.
- Empty evidence avoids model invocation.
- Citation generation and post-generation validation.
- SSE `meta`, `token`, `citations`, `done`, `error` contract.
- Website server proxy with one-time session refresh/retry on agent 401.
- Optional server-authenticity secret that grants no data access.
- Absolute filesystem-path redaction.
- Fake OpenAI boundary tests without paid calls.
- Minimal Docker image and `/health` endpoint.
- OpenAI Responses API provider with `store=False`, bounded output, safe errors and usage fields.
- Live OpenAI Responses API provider smoke tests, including non-streaming and streaming, passed with `gpt-5.4-mini`.
- Process-local token budget guardrail with a documented distributed-quota limitation.

### VERIFIED — exercised against the live corpus

- Approved external-email Supabase login produced a fresh short-lived user token through the development-only website session bridge.
- The agent independently resolved the authenticated profile as `admin`; requesting `committee` correctly produced effective scope `committee`.
- Live RLS-backed committee ICU retrieval returned the speaker-event evidence, including the Union source-article fallback and citations.
- Live OpenAI EFDS synthesis succeeded for the speaker-event query using `gpt-5.4-mini`; the returned citations validated.
- Live admin Slack retrieval and OpenAI synthesis succeeded against canonical messages, with channel and permalink citations.
- Live admin document retrieval succeeded and returned safe relative paths. The requested “EFDS Operating Plan 2026-27” filename was not present; the answer correctly reported that limitation and cited the Master Index and Chair charter instead.
- Live retrieval evaluation authorized all 10 cases with zero forbidden-source leakage. The current corpus run reports 7 cases with final-context evidence, 5 fully passing corpus cases, 2 expected-ID misses, 2 valid no-evidence boundaries, and 1 intentionally unauthorized-adapter boundary. Re-run after corpus changes using the command below; candidate-only IDs are now reported separately from final-context IDs.
- Local FastAPI startup and `/health` were verified with the isolated production dependency set. The user `.venv` remains incomplete for server startup because it has no usable Uvicorn console entry point.

### NOT VERIFIED — requires environment or real accounts

- Latest live retrieval evaluation after the evaluator/routing fixes; this execution lacked the user’s separate `EFDS_AGENT_BEARER_TOKEN` environment variable and correctly reported all 10 cases as auth-denied.
- Website → agent → Supabase → OpenAI production-shaped E2E request.
- Production HTTPS hosting, Vercel environment configuration, and custom-domain routing.
- Local browser E2E through the website chat UI; CLI and direct live retrieval are verified, but the browser path still requires an interactive local run.
- Docker image build/startup because Docker Desktop's Linux engine was unavailable in this environment.
- Hosted agent deployment and `imperial-efds.com` production E2E.
- Local Docker image build/startup was not verified because Docker Desktop's Linux engine was unavailable in this environment; the Dockerfile and command are implemented.

The earlier expired token is no longer the current validation state: an approved external-email website session was used successfully. Microsoft/Imperial login is not required for this development validation path, provided the email is an active approved EFDS external-login exception.

The current public corpus remains empty. A public query therefore returns no authorized evidence and must not invoke OpenAI. Approved public website content should later be ingested by the backend owner rather than added as a second agent corpus.

The live OpenAI provider was verified independently with a bounded synthetic evidence request: `gpt-5.4-mini`, 45 input tokens, 32 output tokens, 77 total tokens, approximately 6.0 seconds. This is not an authenticated EFDS answer and must not be used as the production query-capacity estimate.

The latest agent suite is green (`29 passed, 2 skipped`), with Ruff and Python compilation passing. The two skips are opt-in paid OpenAI tests. Website tests/lint/typecheck/build were previously green; local browser E2E and production deployment remain unverified.

### OPTIONAL / backend-owned follow-up

- Apply backend migrations `0010_unified_retrieval`, `0013_semantic_retrieval`, and `0014_agent_retrieval_contract` through the knowledge-base owner before live agent retrieval.
- Benchmark the deployed unified FTS RPC before considering pgvector.
- Add persistent conversations only with ownership, retention, deletion, and privacy controls.

## Environment

Agent server:

```text
APP_ENV=production
ALLOWED_ORIGINS=https://imperial-efds.com
SUPABASE_URL=https://<project>.supabase.co
SUPABASE_ANON_KEY=<publishable-or-anon-key>
AI_PROVIDER=openai
DEFAULT_MODEL=gpt-5.4-mini
REASONING_MODEL=gpt-5.4-mini
AI_API_KEY=<server-only-key>
MAX_CONTEXT_TOKENS=3500
MAX_OUTPUT_TOKENS=700
MAX_RETRIEVAL_RESULTS=8
OPENAI_DAILY_TOKEN_BUDGET=2500000
AGENT_SHARED_SECRET=<optional-server-only-secret>
```

Website server environment:

```text
NEXT_PUBLIC_SITE_URL=https://imperial-efds.com
EFDS_AGENT_URL=https://<hosted-agent-url>
EFDS_AGENT_SHARED_SECRET=<same-optional-secret>
```

Never configure `AI_API_KEY`, `SUPABASE_SERVICE_ROLE_KEY`, `DATABASE_URL`, access tokens, or refresh tokens with a `NEXT_PUBLIC_` prefix.

## Local production-shaped startup

```powershell
docker build -t efds-agent .
docker run --rm -p 8000:8000 --env-file .env efds-agent
```

The production command is `uvicorn efds_agent.api.app:app --host 0.0.0.0 --port $PORT`; development `--reload` is not used.

## Hosting recommendation

Default: Render Web Service from this Dockerfile. It supports FastAPI/Docker services, custom domains, configured secrets, and HTTP health checks; configure `/health` and the service port. [Render web services](https://render.com/docs/web-services), [Render health checks](https://render.com/docs/health-checks)

Fallback: Google Cloud Run if Imperial/EFDS already has a Google Cloud account and someone is comfortable managing IAM and billing. The container already binds to `0.0.0.0` and respects `PORT`, matching Cloud Run’s container contract. [Cloud Run container contract](https://cloud.google.com/run/docs/container-contract)

Do not expose a public agent URL to browsers. Vercel server-side `/api/chat` should call the hosted service. A custom `agent.imperial-efds.com` hostname is optional; the provider hostname can remain server-only.

## Abuse and cost controls

- Keep Vercel/hosting rate limits in front of `/api/chat`.
- Keep agent result/context/output limits and request timeouts enabled.
- Do not retry OpenAI automatically after provider failures.
- Do not call OpenAI when retrieval returns no authorized evidence.
- Use the optional shared secret to reduce arbitrary direct traffic; it is not an authorization mechanism.
- Review provider usage and 401/403/5xx rates without logging prompts, JWTs, keys, or private source bodies.

Redis/Celery/Kafka are unnecessary at current society traffic. If rate limiting later needs shared state, prefer the hosting edge or Vercel limits first.

## Fresh session workflow

The normal path is website authentication. Supabase SSR `getUser()` validates/refreshes the cookie session; the website then forwards the current short-lived access token server-to-server. The route retries exactly once after `refreshSession()` if the agent returns 401, then asks the user to sign in again.

For a developer-only direct smoke test:

```powershell
$env:EFDS_AGENT_BEARER_TOKEN = "<short-lived Supabase access token>"
python scripts/live_smoke.py --scope committee --query "What do I need to do before inviting an external speaker?" --show-plan --show-results --show-citations
```

Do not print, commit, or persist the token.

When the local website is running and the developer has authenticated through
the approved external flow, open the development-only route in the same
authenticated browser. Calling it from a separate PowerShell request does not
include the browser's cookies and correctly returns `401`:

```powershell
```javascript
fetch("/api/dev/access-token").then((response) => response.json()).then((payload) => navigator.clipboard.writeText(payload.access_token));
```

Then read the clipboard into the current PowerShell process without echoing it:

```powershell
$env:EFDS_AGENT_BEARER_TOKEN = Get-Clipboard
python scripts/live_smoke.py --scope committee --query "What do I need to do before inviting an external speaker?" --show-citations
Remove-Item Env:EFDS_AGENT_BEARER_TOKEN
```
```

The route returns 404 outside development and uses `Cache-Control: no-store`.
Clear the clipboard after use; never print or persist the token.
