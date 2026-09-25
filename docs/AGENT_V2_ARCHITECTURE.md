# EFDS Agent V2 architecture

Compiled 25 Sep 2026. This document describes the change from V1 (single-pass
retrieval, one model call, citation-ID validation) to V2 (bounded agentic
retrieval with query planning, hybrid search, and post-generation citation
verification), and states plainly what the agent deliberately still does not do.

V1 remains described in [ARCHITECTURE.md](ARCHITECTURE.md) for the parts that did
not change: runtime ownership, the auth handshake, and the SSE event contract.

## 1. What "bounded agentic retrieval" means here

The design constraint was *"the model may call a capped number of READ-ONLY
retrieval tools (multi-query, decomposition, follow-up search) before one final
answer"*. That is a **bounded** loop, not an autonomous agent:

| Capability | V2 behaviour |
| --- | --- |
| Retrieval calls per request | Fixed. One planner call, one retrieval call that carries up to `SUB_QUERY_LIMIT` (default 3) queries, one synthesis call, one verification call. |
| Tool surface | None. The model never chooses a tool, a table, a source mode, or a role. It emits text and, in two structured calls, JSON matching a fixed schema. |
| Write actions | None. Unchanged from V1. |
| Loop termination | Structural, not model-decided. There is no loop: the sequence is linear and its length is fixed at request time. |

This matters because the reason to be careful is unchanged. The agent handles
retrieved content it does not control, and that content can contain instructions.
The *lethal trifecta* (Simon Willison, 2025-06-16) is private data + untrusted
content + an exfiltration path. EFDS has private data and untrusted content, so
the design removes the third leg: no write tools, no network egress chosen by the
model, no URLs fetched on the model's behalf. A capability-based design such as
CaMeL (arXiv:2503.18813v2) reaches a similar conclusion by construction; here the
capability set is empty by default rather than enforced by an interpreter. CaMeL
reports 77% task completion on AgentDojo versus 84% undefended — capability
enforcement costs some capability, which is why the surface was kept at zero
instead of being made configurable.

## 2. Request pipeline

```
efds-site browser UI
  -> server-side Supabase session and scope check
  -> HTTPS/SSE proxy
efds-agent FastAPI
  -> verify Supabase user and active profile; requested scope may only narrow
  -> [1] query planner            (cheap tier, gated, structured output)
  -> [2] KnowledgeRetrievalGateway (one hybrid multi-query RPC, user JWT)
  -> [3] build_context             (spotlighted, datamarked evidence envelope)
  -> [4] synthesis                 (default tier; reasoning tier when decomposed)
  -> [5] citation verification     (cheap tier, structured output)
  -> citations + done events
efds-knowledge-base PostgreSQL/RLS/retrieval_units
  -> SECURITY DEFINER public entry point, or SECURITY INVOKER role-aware one
```

### Stage 1 — planner (`retrieval/query_planner.py`)

Two jobs: resolve a question that depends on conversation history into a
standalone question, and decompose a multi-part question into sub-queries.

The planner is **gated**, because unconditional query rewriting is a measured
regression, not a neutral transformation. It runs only when the question
*needs* it:

- `elliptical_follow_up` — the question contains a referential fragment
  ("and how long does it take?") *and* there is usable history.
- `multi_part_question` — several distinct asks, or multiple interrogatives,
  above a length floor.

Everything else is searched exactly as asked (`self_contained_single_turn`), so
an ordinary lookup spends no extra model call and cannot be degraded by
rewriting. The gate is deterministic code, not a model decision.

The planner uses the **cheap tier** (`TaskType.PLANNING`), returns JSON matching
a fixed schema, and degrades to the raw question on any error. Planning is
best-effort by construction: a planner outage costs retrieval quality, never an
answer.

### Stage 2 — retrieval gateway (`retrieval/gateway.py`)

One call, carrying the rewritten question plus sub-queries. Three entry points:

- `search_retrieval_units_multi` — authenticated, `SECURITY INVOKER`, so RLS
  stays the authoritative boundary. Multi-query lexical RRF (k=60) fused with a
  rank-based semantic branch.
- `search_retrieval_units_public` — anonymous, `SECURITY DEFINER` with a pinned
  `search_path` and a public predicate written as a literal constant mirroring
  the `retrieval_units_public_select` policy. It takes no role argument, so it
  cannot be asked for anything but public rows.
- `search_retrieval_units_v1` — the V1 entry point, kept as a fallback so a
  staged deployment stays usable. The chosen strategy is recorded in the trace as
  `retrieval_strategy`, so a silent fallback is visible rather than assumed.

The gateway picks the public or authenticated entry point from whether a bearer
token is present, because that is what PostgREST will treat the request as. A
role-scoped context with no token is anonymous to the database.

Fusion is rank-based rather than score-based on purpose. `ts_rank_cd` and cosine
distance are not commensurable, so averaging them would let one scale dominate.
Rank fusion avoids that, but it introduces its own failure: without a similarity
floor, *every* corpus row receives semantic RRF credit, and a weak semantic match
scores near a strong lexical one. Measured on the sandbox corpus, a query with no
similarity floor returned the entire corpus (6/6); with `semantic_floor` plus a
candidate cap it returned 1. The floor is a parameter (`SEMANTIC_FLOOR`) and must
be calibrated against the eval suite before being moved.

The query embedding is pinned to the profile the corpus was indexed with, read
from `retrieval_embedding_profile()`. On a mismatch the semantic branch is
skipped with a warning rather than comparing vectors from different models —
cross-model cosine similarity is numerically valid and semantically meaningless.

Returns `content`, not the `ts_headline` snippet V1 returned. V1's two-fragment,
50-word snippets meant the model was asked to ground an answer in text it could
not actually read.

### Stage 3 — context (`agent/context.py`, `citations/formatter.py`)

Retrieved text is framed as **lowest-authority input**. Three mechanisms, all
from the prompt-injection literature:

1. **Instruction hierarchy** stated in the policy (arXiv:2404.13208): system
   policy > user question > retrieved data.
2. **Spotlighting via datamarking** (arXiv:2403.14720, reported to cut attack
   success rates from above 50% to under 2% on their benchmarks): every line of
   retrieved content is prefixed with a per-request unpredictable marker, and
   the envelope is delimited by that marker.
3. **Anti-forgery**: because the marker is unpredictable per request, retrieved
   content cannot close the envelope it sits inside. Any lookalike delimiter in
   source content — including the `END_` form — is neutralised, and the marker is
   stripped from source text.

The marker is generated once per request and never reused.

### Stage 4 — synthesis (`providers/openai.py`)

One Responses API call. Model tier comes from measured question difficulty, not
from caller role:

| Tier | Model | When |
| --- | --- | --- |
| small | `gpt-5.6-luna` | planning, verification |
| default | `gpt-5.6-terra` | all synthesis (the default) |
| reasoning | `gpt-6-astra` | configured but **unused** unless escalation is enabled |

Escalation to the flagship tier on decomposed questions is implemented and off by
default (`ESCALATE_DECOMPOSED_QUESTIONS=false`). The reasoning tier exists so a
live eval can test whether it earns its cost; until it demonstrably does, every
request is answered on the default tier, which keeps cost predictable. Keying any
future escalation on difficulty rather than caller role is the point — a member's
single-fact lookup should never pay flagship prices.

Prompt caching requires a stable, cacheable prefix of at least 1,024 tokens on
the current generation. V1's static prompt is roughly 250 tokens, so the cache
never engaged. V2 restructures the request so instructions are the cacheable
prefix, fixed `prompt_cache_key` keeps load on one shard, and `cached_tokens` is
recorded on every call so a silently broken cache is visible.

### Stage 5 — citation verification (`citations/verification.py`)

V1's `validate_answer()` only removed citation IDs that were not in the package.
That catches a fabricated ID; it says nothing about whether a claim is supported
by the source it cites. V2 adds a post-generation audit: the drafted answer's
cited claims are checked against their actual evidence spans, and unsupported
citations are removed.

There is no embedded model here, and no local entailment model. Published
citation-judge accuracy is roughly 80% macro-F1 (AttributionBench; HHEM-2.1-Open),
which is a real limit and the reason refusal is treated as a first-class outcome
rather than a failure:

| Outcome | Behaviour |
| --- | --- |
| All cited claims supported | answer unchanged, trace `verification_note = all_supported` |
| Some unsupported | affected citations removed, limitation added, corrected answer returned |
| No cited claim supported | answer **withheld** and replaced with an explicit refusal; `insufficient_evidence = true` |
| Verifier unreachable | answer returned unverified, trace records `verifier_error` |
| Answer cites nothing resolvable | sent to no verifier at all |

Verification is advisory-on-failure and load-bearing-on-success: a verification
outage degrades to V1 behaviour and says so in the trace, while a successful
verification that finds nothing supported will withhold the answer.

The streaming path cannot recall tokens already sent. The `done` event carries
the authoritative answer and the correction is flagged (`correction: true`,
`answer_withheld_by_verification`), so a client must render `done.answer` as
final rather than the concatenation of `token` events.

## 3. Security model

Unchanged from V1 in kind, extended in one place.

- The agent still accepts only a bearer token, query, bounded conversation,
  requested scope, and a server-known source mode. Never SQL, table names,
  PostgreSQL roles, visibility overrides, model names, or service credentials.
- RLS remains the final authorization boundary. The multi-query RPC is
  `SECURITY INVOKER` for that reason.
- The one new `SECURITY DEFINER` function is the anonymous public entry point. It
  takes no role argument, pins `search_path`, and its predicate is a literal
  constant rather than a role-helper call — so it cannot be used to reach
  anything but public rows, and it does not depend on `has_efds_role`, which
  anonymous callers are (correctly) not allowed to execute.
- Retrieved text remains untrusted input, now with datamarking and anti-forgery
  in addition to delimiters.

## 4. Why retrieval parity mattered more than prompt work

`efds-knowledge-base` benchmarks hybrid retrieval at **Recall@10 = 0.7059**. V1's
agent called a lexical-only RPC (`websearch_to_tsquery`), so it could not reach
that number by construction. The RPC wrapper and the benchmarked Python path had
also drifted: `search_retrieval_units` (0010) grants different source-type
authority bonuses than Python's `_search_lexical`, and excludes a different set of
source types for non-admins. Parity could not be certified.

The highest-value change was therefore to expose the fused path rather than to
tune prompts: migration `0023_public_retrieval_and_multi_query.py` in
`efds-knowledge-base` adds the multi-query hybrid entry point, the anonymous
public entry point, and `retrieval_embedding_profile()`.

That migration also fixes two live defects found while verifying against the
production project:

- **D1** — `anon` calling the V1 search RPC returned `42501 permission denied for
  function has_efds_role`. Migration `0018` revoked `has_efds_role` from `anon`
  while `search_retrieval_units` (granted to `anon`) calls it. A `CASE`
  short-circuit does not help: PostgreSQL checks `EXECUTE` at plan time. The
  whole public retrieval path was dead, independently of the agent never having
  been deployed.
- **D2** — `anon` reading `public_knowledge_resources` returned
  `42501 permission denied for table knowledge_resources`. The view is evaluated
  with invoker rights in production despite carrying no `security_invoker`
  setting, so anonymous callers fall through to the base table.

Both were confirmed against the production project with the publishable key, not
inferred from code.

## 5. Cost model

Per request, worst case:

| Call | Tier | Notes |
| --- | --- | --- |
| planner | small | only when the gate fires |
| synthesis | default, or reasoning when decomposed | the only unbounded-length call |
| verification | small | skipped when the answer cites nothing resolvable |

The two structured calls run on the cheap tier, so the dominant cost remains one
synthesis call. The gate means an ordinary lookup makes exactly one model call,
the same count as V1.

## 6. What this agent still does not do

Stated explicitly, because "agentic" invites the assumption:

- No autonomous tool loop, no ReAct loop, no multi-agent orchestration.
- No write tools, filesystem tools, web search, or arbitrary SQL.
- No model-chosen model, role, table, or source mode.
- No self-critique loop over the answer; verification is a single external check
  with a fixed schema, not a revision cycle.
- No cross-encoder reranker. Rank fusion plus a similarity floor is what the
  evidence supports at this corpus size; a reranker is the next step if eval
  shows the floor cannot be calibrated to separate the head from the tail.

## 7. Sources

Security: arXiv:2404.13208 (instruction hierarchy), arXiv:2403.14720
(spotlighting/datamarking), arXiv:2503.18813v2 (CaMeL), Simon Willison
"The lethal trifecta" (2025-06-16), OWASP LLM Top 10 v2.0, NIST AI 100-2e2025.

Retrieval: arXiv:2501.09136, arXiv:2506.10408, Self-RAG (arXiv:2310.11511), CRAG
(arXiv:2401.15884), Adaptive-RAG (arXiv:2403.14403), A-RAG (arXiv:2602.03442).

Verification: AttributionBench, HHEM-2.1-Open.

Figures attributed to those papers are as reported in them; they were not
independently reproduced here. The retrieval figures that *are* measured
(Recall@10 0.7059, the 6/6 → 1 semantic-floor result) come from this repository's
own benchmark and the sandbox database.
