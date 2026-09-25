# EFDS Agent evaluation

The agent suite has two halves, matching the two architectures. V2 adds
planning, hybrid retrieval, and post-generation citation verification, so its
outcomes are evaluated separately from V1's.

## V2 agent suite

```powershell
.\.venv\Scripts\python.exe scripts/run_agent_eval.py
```

This is deterministic and credential-free: fixture retrieval, no paid model
calls. It exits **non-zero when any case fails**, so it can gate a build.

Cases cover retrieval miss, generation miss, invalid citation, insufficient
evidence, prompt-injection evidence, and member/admin isolation. Expected
negative cases are labelled in the fixture (`expected_retrieval_miss`,
`expected_generation_miss`, `expected_invalid_citation`) rather than counted as
product regressions.

## Unit tests

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

These cover the V2 behaviour that the fixture suite cannot reach without a live
model: the planner's gate and fallbacks, the evidence envelope's anti-forgery
behaviour, citation-verification outcomes including refusal and verifier outage,
and model-tier selection including escalation bounds.

## Continuous integration

`.github/workflows/agent-checks.yml` runs, on every push to `main` and every pull
request: `ruff check`, `ruff format --check`, `pytest`, the V2 agent suite, and an
offline smoke of the retrieval harness. No step requires a credential or makes a
paid call.

Live hit-rate evaluation is deliberately not in CI: it needs a real bearer token
and the production backend, so it stays manually triggered.

## Retrieval corpus harness

Run the deterministic agent evaluation without paid model calls:

```powershell
.\.venv\Scripts\python.exe scripts/run_agent_eval.py
```

The fixture set is separate from the retrieval benchmark and includes a
pre-term speaker process, an intentional retrieval miss, a generation miss,
invalid citation output, prompt-injection evidence, and member/admin isolation.
It reports observed `RETRIEVAL_MISS`, `GENERATION_MISS`, invalid citation,
insufficient-evidence, and authorization-leakage categories. Expected negative
cases are labelled in the fixture rather than counted as product regressions.

The existing retrieval-only corpus harness remains available:

```powershell
.\.venv\Scripts\python.exe scripts/run_eval.py --retrieval-only
```

Its default `--cases` is the `evals/cases` directory. `agent_preterm_live.json`
uses the separate HTTP-endpoint schema that `run_live_agent_eval.py` consumes, so
the loader skips schema-incompatible files and reports them under
`skipped_case_files` instead of failing on the first case it cannot run.

Offline, private-scope cases report `private_scope_simulation_without_bearer`:
without a bearer token there is no RLS-backed evidence to retrieve, so a low
offline hit rate is expected and is not a regression. Hit rates are only
meaningful with `--live`.

For a live RLS retrieval evaluation, use a short-lived token in the current
PowerShell process only:

```powershell
$env:EFDS_AGENT_BEARER_TOKEN = Get-Clipboard
.\.venv\Scripts\python.exe scripts/run_eval.py --retrieval-only --live
Remove-Item Env:EFDS_AGENT_BEARER_TOKEN
```

Live OpenAI calls are not part of normal tests. The opt-in end-to-end smoke
test is:

```powershell
.\.venv\Scripts\python.exe scripts/smoke_test_agent.py --scope committee --query "What do we need to do before inviting an external speaker?"
```

Evaluation categories:

- retrieval miss: expected stable evidence is absent from the ContextPackage;
- generation miss: evidence is present but the mock/generated answer fails its rubric;
- invalid citation: the model emits an unknown S-ID and post-processing removes it;
- insufficient evidence: no authorized evidence must produce abstention;
- authorization leakage: forbidden source types or admin-only evidence reach the answer.

V2 adds these outcomes:

- planner gate: a self-contained question is searched as asked and spends no
  planning call; only a history-dependent or multi-part question is rewritten;
- planner degradation: a planner error or unparsable plan falls back to the raw
  question rather than failing the request;
- envelope integrity: retrieved content cannot forge or close the evidence
  boundary, and the per-request marker is stripped from source text;
- citation verification: an unsupported claim loses its citation, and an answer
  with no supported cited claim is withheld rather than asserted;
- verification degradation: a verifier outage returns the answer unverified and
  records `verifier_error` in the trace.

Subjective correctness, unsupported-claim review, authority conflicts, and
temporal correctness remain manual rubric checks until there is enough dogfood
data to justify a separate judge system. Note that the citation verifier is
itself a model call, so its own false-negative rate bounds the guarantee: a
withheld answer means the audit found nothing supported, not that the answer is
false.

## Live contract and dogfood evaluation

The fixed RPC contract is checked without applying migrations:

```powershell
.\.venv\Scripts\python.exe scripts/rpc_health.py --require-ready
```

The live holdout set is `evals/cases/agent_preterm_live.json` (17 frozen
holdout queries plus one deliberate no-answer case). Retrieval-only evaluation
is the default and uses the production HTTP gateway:

```powershell
$env:EFDS_AGENT_BEARER_TOKEN = Get-Clipboard
.\.venv\Scripts\python.exe scripts/run_live_agent_eval.py
Remove-Item Env:EFDS_AGENT_BEARER_TOKEN
```

Add `--with-model` only for an explicitly approved live OpenAI run. Results are
written to `evaluation/agent_v1_live_eval_results.json` and classify retrieval,
generation, citation, abstention, authorization, and integration failures.
The report includes stable retrieval IDs and bounded citation previews for
manual claim-support review; it does not claim automated hallucination
detection.

For top-10 parity, first export the KB-owned `search_retrieval(...,
mode="hybrid")` results with `efds-knowledge-base/scripts/export_canonical_retrieval.py`,
then run `scripts/compare_retrieval_parity.py` with the same scope, source mode,
query set, and K. Any unavailable v1 RPC is recorded as an integration failure;
the agent never falls back to deprecated source adapters.
