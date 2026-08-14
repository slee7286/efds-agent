# EFDS Agent V1 evaluation

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

Subjective correctness, unsupported-claim review, authority conflicts, and
temporal correctness remain manual rubric checks until there is enough dogfood
data to justify a separate judge system.

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
