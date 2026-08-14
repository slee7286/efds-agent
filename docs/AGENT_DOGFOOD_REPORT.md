# EFDS Agent live contract and dogfood report

Date: 2026-08-14

## Current result

The live/staged Supabase project does not currently expose the v1 retrieval
contract. A read-only POST to
`/rest/v1/rpc/search_retrieval_units_v1` returned HTTP 404 with PostgREST
`PGRST202` (“function ... was not found in the schema cache”). The health
command records this as `rpc_not_found`; no migration was applied by this
milestone.

The local migration head is `0014_agent_retrieval_contract`, while the
configured database currently reports `alembic current = 0013_semantic_retrieval`.
The exact manual command is:

```powershell
cd ..\efds-knowledge-base
.\.venv\Scripts\python.exe -m alembic upgrade head
```

After applying it, refresh/reload PostgREST if required and run:

```powershell
cd ..\efds-agent
.\.venv\Scripts\python.exe scripts/rpc_health.py --require-ready
```

For response-field verification, repeat with an authorized admin token and
`--require-schema`; anonymous HTTP 200 with zero rows only proves RPC
availability, not the returned row shape.

## Semantic-path finding

The benchmark executes:

```text
query -> KB OpenAI embedding provider
      -> search_retrieval_units_semantic
      -> semantic-primary exact-title/acronym rescue
      -> benchmark result order
```

Migration 0014 currently executes:

```text
query -> search_retrieval_units_v1(text arguments)
      -> lexical search_retrieval_units
```

The v1 function has no query-embedding argument and cannot perform the
benchmarked semantic-primary path inside PostgreSQL. This is a concrete
contract blocker, not a ranking-tuning request. The agent does not recreate
embedding or ranking logic. A KB-owned retrieval endpoint/RPC implementation
must make the benchmarked path canonical before parity can be certified.

## Live runs

No valid user JWT was supplied for this run. The following are therefore not
claimed as passed:

- admin/member/public JWT/RLS isolation;
- scope-escalation live test;
- retrieval parity rate;
- OpenAI smoke answer/citations;
- website browser-to-SSE end-to-end run;
- live latency or token percentiles.

The scripts are present and opt-in. They write only bounded IDs, titles,
previews, status labels, and timings; they do not write tokens or full source
documents.

The checked-in machine-readable parity status is
`evaluation/agent_v1_retrieval_parity.json`; its comparison count is zero and
its rates are `null` because the contract was unavailable, not because the
agent was treated as a retrieval miss.

## Evaluation set and failure labels

`evals/cases/agent_preterm_live.json` contains 18 cases: the 17 frozen
pre-term holdout queries plus an intentional no-answer submarine question.
`run_live_agent_eval.py` supports retrieval-only evaluation by default and
requires `--with-model` for OpenAI. It distinguishes:

- `RETRIEVAL_MISS`
- `GENERATION_MISS`
- `CITATION_MISS`
- `ABSTENTION_FAILURE`
- `AUTHORIZATION_FAILURE`
- `INTEGRATION_FAILURE`

The machine-readable output path is
`evaluation/agent_v1_live_eval_results.json`; this checkout records a blocked
pre-run result rather than inventing live measurements.

## Readiness recommendation

- Software: conditional ready; automated tests pass, but the live contract is
  not available in the configured project.
- Admin dogfood: NO-GO until migration 0014 is applied, RPC health is ready,
  and one real JWT retrieval smoke passes.
- Committee beta: NO-GO pending semantic-path parity, live authorization
  evidence, and reviewed answer/citation results.
- Member/public: NO-GO pending scope-specific corpus and route validation.
- Full institutional: NO-GO; Slack, Meetily, and operational corpus maturity
  remain uncertified.

## Exact run order after the blocker is cleared

1. Apply 0014 and verify `alembic current`.
2. Run `scripts/rpc_health.py --require-ready`.
3. Export KB canonical results with
   `scripts/export_canonical_retrieval.py`.
4. Put a fresh short-lived access token in
   `$env:EFDS_AGENT_BEARER_TOKEN` and run
   `scripts/compare_retrieval_parity.py`.
5. Run retrieval-only smoke queries from `scripts/dogfood_queries.json`.
6. Run `scripts/smoke_test_agent.py` for one grounded OpenAI answer.
7. Start the agent on port 8000 and the website on port 4587.
8. Test private website chat, citations, no-answer behavior, a follow-up, and
   requested-scope narrowing/escalation.
9. Run `scripts/run_live_agent_eval.py`; inspect its JSON output and manually
   label unsupported claims.
10. Remove the token from the PowerShell environment.
