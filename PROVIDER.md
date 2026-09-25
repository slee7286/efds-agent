# OpenAI provider

The official OpenAI Python SDK with the Responses API is the only runtime model
path. There is one provider and no fallback provider.

## Model tiers

Model selection is a configuration lookup, not a model decision. `choose_model()`
maps a task type to a tier:

| Task type | Tier | Setting | Default |
| --- | --- | --- | --- |
| `SYNTHESIS` | default | `DEFAULT_MODEL` | `gpt-5.6-terra` |
| `REASONING` | flagship | `REASONING_MODEL` | `gpt-6-astra` |
| `PLANNING` | cheap | `SMALL_MODEL` | `gpt-5.6-luna` |
| `VERIFICATION` | cheap | `SMALL_MODEL` | `gpt-5.6-luna` |

Planning and citation verification are short, structured and latency-tolerant,
so they run on the cheap tier. Synthesis runs on the default tier. Escalation to
the flagship tier on decomposed questions exists but is **off by default**
(`ESCALATE_DECOMPOSED_QUESTIONS=false`): the cost cap matters more than the
marginal quality gain, so the flagship tier is configured but unused unless
enabled. Turn it on only if a live eval shows the default tier losing multi-hop
answers.

The previous defaults (`gpt-5.4-mini`) were three generations stale.

## Request shape

- **Cacheable prefix.** Instructions are sent as `instructions` and evidence
  precedes the question in `input`, so nothing variable sits at the front of the
  request. `prompt_cache_key` is fixed (`efds-agent-v2`) to keep load on one
  shard. Prompt caching needs a stable prefix of at least 1,024 tokens on the
  current generation; the static policy alone is far shorter, so the cache turns
  on once the evidence envelope is appended.
- **Cache telemetry.** `cached_tokens` is read from
  `usage.input_tokens_details` and recorded on every trace, so a silently broken
  cache is visible rather than assumed.
- **Structured output.** Planning and verification pass a JSON schema; it is sent
  as `text.format` with `type=json_schema`, `strict=true`, and a stable `name`
  from `structured_output_name`.
- **Effort.** `reasoning.effort` defaults to `low`: these are bounded
  extraction and audit tasks over supplied evidence, not multi-step deduction.
- **Storage.** Requests set `store=False`; EFDS remains the source of truth.
- **Streaming.** Responses API `stream=True`, translated into EFDS `token`
  events. The `done` event carries the authoritative answer.

## Configuration

`AI_PROVIDER=openai`, `DEFAULT_MODEL=`, `SMALL_MODEL=`, `REASONING_MODEL=`,
`ESCALATE_DECOMPOSED_QUESTIONS=`, `AI_API_KEY=`.

## Tests

Normal tests inject fake Responses API clients and make no network or paid calls.
Live provider tests are opt-in via `RUN_LIVE_AI_TESTS=1` and use a small bounded
prompt.
