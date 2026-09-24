# EFDS Agent V1

Read-only, grounded EFDS knowledge assistant for internal pre-term dogfooding.
The agent authenticates the user, retrieves once through the canonical
`efds-knowledge-base` Supabase RPC, performs one bounded OpenAI Responses API
synthesis call, validates citations, and streams the result through
`efds-site`.

It does not implement retrieval ranking, arbitrary SQL, filesystem access, web
search, mutations, service-role retrieval, or autonomous tool loops.

## Local setup

Use Python 3.12+:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
Copy-Item .env.example .env
```

Start the agent on its Docker/default port 8000:

```powershell
.\.venv\Scripts\python.exe -m uvicorn efds_agent.api.app:app --reload --port 8000
```

Start the website separately from `efds-site` on its configured port 4587:

```powershell
npm.cmd install
npm.cmd run dev
```

The knowledge-base repository remains the schema/ingestion/retrieval owner;
apply its migrations and run its embedding/index diagnostics from that
repository under its controlled workflow. The agent does not open its
database directly.

## Environment

Required for real private retrieval and synthesis:

```text
SUPABASE_URL
SUPABASE_ANON_KEY       # public/publishable key only
AI_API_KEY              # server-only OpenAI key
```

Important optional settings:

```text
DEFAULT_MODEL=gpt-5.4-mini
REASONING_MODEL=gpt-5.4-mini
AGENT_RETRIEVAL_K=10
AGENT_MAX_RETRIEVAL_K=20
MAX_CONTEXT_TOKENS=3500
MAX_CONTEXT_ITEMS=10
MAX_CONVERSATION_TURNS=4
MAX_CONVERSATION_CHARS=4000
REQUEST_TIMEOUT_SECONDS=15
AGENT_SHARED_SECRET=       # caller authenticity only; never grants data access
```

The website needs `NEXT_PUBLIC_SUPABASE_URL`, a publishable/anon browser key,
`EFDS_AGENT_URL=http://localhost:8000`, and optionally the matching
`EFDS_AGENT_SHARED_SECRET`. `OPENAI_API_KEY` is never sent to the browser.

## API

`GET /health` reports process, retrieval/model configuration, and mode status.
`GET /v1/health/retrieval` performs a safe read-only RPC/schema probe and
reports whether migration `0014_agent_retrieval_contract` is visible. It does
not apply migrations. `POST /v1/retrieval` is an authenticated,
retrieval-only dogfood endpoint using the same gateway as QA.
`POST /v1/query` accepts a query, optional lower requested scope, the default
`preterm_knowledge` mode, and at most four bounded conversation turns.
`POST /v1/query/stream` emits:

```text
meta -> token* -> citations -> done
```

Retrieval, authentication, authorization, provider, and invalid-request errors
are kept distinct. Zero evidence emits a deterministic insufficient-evidence
answer without calling OpenAI.

EFDS account roles and retrieval scopes are distinct. A newly created `member`
account is limited to `public` retrieval. A society-verified `efds_member`
account may request `member` retrieval; committee and admin roles retain their
respective higher scopes. The agent reads the stored profile role for every
authenticated request and rejects a scope above it, even when a caller bypasses
the website.

## Source modes

- `preterm_knowledge`: ICU/structured knowledge, approved `01_governance`
  documents, and populated approved operational records; internal beta only.
- `full_institutional`: structural future mode for authorized documents, Slack,
  sender-limited Outlook mail, meeting notes, and operational truth; admin-only
  and not certified.
- `committee_tickets`: recent messages from enabled public Slack channels,
  returned by the backend's committee-only RPC for source-backed ticket drafts.
  No private channels, meeting notes, or documents are available in this mode.
- `admin_outlook_tickets`: current messages from the backend's two configured
  Outlook senders, returned by a dedicated admin-only RPC for private ticket
  proposals. This mode has no evidence until the optional collector is
  connected and a real sync succeeds.
- `public`: lower policy mode subject to public RLS/publication.

The latest approved retrieval benchmark is Hit@1 0.4118, Hit@10/Recall@10
0.7059, and MRR 0.5139. This is sufficient for internal dogfood with mandatory
abstention, not general production certification.

## Tests and evaluation

```powershell
.\.venv\Scripts\python.exe -m pytest
.\.venv\Scripts\ruff.exe check src tests scripts
.\.venv\Scripts\python.exe scripts/run_agent_eval.py
.\.venv\Scripts\python.exe scripts/run_eval.py --retrieval-only
```

Normal tests use provider fixtures and do not require live OpenAI calls. The
opt-in live end-to-end smoke test is:

```powershell
$env:EFDS_AGENT_BEARER_TOKEN = Get-Clipboard
.\.venv\Scripts\python.exe scripts/smoke_test_agent.py --scope committee --query "What do we need to do before inviting an external speaker?"
Remove-Item Env:EFDS_AGENT_BEARER_TOKEN
```

Use a short-lived token from the authenticated website session only; never put
it in `.env`, source control, or logs.

## Live contract validation

Apply the migration manually from the knowledge-base repository:

```powershell
cd ..\efds-knowledge-base
.\.venv\Scripts\python.exe -m alembic upgrade head
.\.venv\Scripts\python.exe -m alembic current
```

The expected head is `0014_agent_retrieval_contract`. From this repository,
check the deployed PostgREST contract without mutating it:

```powershell
cd ..\efds-agent
.\.venv\Scripts\python.exe scripts/rpc_health.py --require-ready
```

Use `--require-schema` with a fresh authorized user token to require that at
least one RLS-visible row validates every v1 provenance/authority field:

```powershell
$env:EFDS_AGENT_BEARER_TOKEN = Get-Clipboard
.\.venv\Scripts\python.exe scripts/rpc_health.py --require-ready --require-schema
Remove-Item Env:EFDS_AGENT_BEARER_TOKEN
```

The configured staged project was observed returning HTTP 404/PGRST202 for the
v1 RPC, so live retrieval and model smoke tests remain blocked until migration
0014 is applied and the schema cache is refreshed.

The KB-owned canonical export and agent parity comparison are:

```powershell
cd ..\efds-knowledge-base
.\.venv\Scripts\python.exe scripts/export_canonical_retrieval.py evaluation/retrieval_v2_preterm_holdout.json --output ..\efds-agent\evaluation\canonical_preterm.json
cd ..\efds-agent
$env:EFDS_AGENT_BEARER_TOKEN = Get-Clipboard
.\.venv\Scripts\python.exe scripts/compare_retrieval_parity.py --canonical evaluation/canonical_preterm.json --output evaluation/agent_v1_retrieval_parity.json
Remove-Item Env:EFDS_AGENT_BEARER_TOKEN
```

Retrieval-only dogfood uses `scripts/live_smoke.py`; the real OpenAI smoke is
`scripts/smoke_test_agent.py`. The 18-case live evaluation is
`scripts/run_live_agent_eval.py`, with `--with-model` required to call OpenAI.

## Dogfood starter procedure

1. In `efds-knowledge-base`, verify the approved 01_governance embedding report
   shows 21/21 eligible units embedded.
2. Start the agent and website, sign in as an admin, and open private chat.
3. Ask the external-speaker, event-risk, governance-document, society-finance,
   room-booking, and committee-responsibility questions.
4. Inspect answer citations and the admin-only retrieval diagnostics route.
5. Ask an intentionally absent policy question and verify abstention.
6. Request a lower scope and verify it narrows access; request a higher scope
   and verify 403/no leakage.
7. Ask a follow-up such as “How early do we need to do that?” and verify the
   bounded prior turns are used.
8. Record each failure as retrieval miss, generation miss, invalid citation,
   insufficient evidence, authorization leakage, retrieval dependency, or
   model provider failure.

## Readiness

Software integration: ready for internal verification. Admin dogfood:
ready-with-limitations once live RLS/model smoke passes. Committee/member beta:
not certified by this milestone. Public chat remains constrained to public
material but is not a general institutional certification. Full institutional
mode is structural and not certified. See the architecture and evaluation
documents for the ownership and security details.
