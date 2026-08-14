# EFDS agent evaluations

Cases are JSON rather than exact natural-language snapshots. Retrieval tests remain deterministic and separate from model-quality tests. `evals/cases/v2_corpus.json` was derived from the live backend corpus and uses stable source IDs observed there.

Run the no-model baseline with:

```powershell
python scripts/run_eval.py --retrieval-only
```

For actual RLS-backed evaluation, provide a fresh short-lived token and run:

```powershell
$env:EFDS_AGENT_BEARER_TOKEN = "<short-lived token>"
python scripts/run_eval.py --retrieval-only --live
```

The preferred local workflow is to sign in through `efds-site` first and request its development-only `/api/dev/access-token` route. Never print, save, or commit the returned token.

Metrics include authorization status, retrieval hit, expected source-type hit, expected source-ID hit in the final context, candidate-only expected-ID hit, citation presence/validity (with a separate applicability count), forbidden-source leakage, answer support, and per-case latency. A candidate-only ID hit means the row was fetched but did not necessarily survive context selection; use the final-context ID metric for evidence quality. An empty operational table is a passing no-evidence condition, not an answer failure. No normal case requires a paid model.
