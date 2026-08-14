# V1 OpenAI provider

V4 uses the official OpenAI Python SDK and the Responses API as the only runtime model path.

- Provider: OpenAI Responses API
- Default model: `gpt-5.4-mini`
- Reasoning model: `gpt-5.4-mini`
- Configuration: `AI_PROVIDER=openai`, `DEFAULT_MODEL=`, `REASONING_MODEL=`, `AI_API_KEY=`
- Streaming: Responses API `stream=True`, translated into EFDS `token` events
- Storage: requests set `store=False`; EFDS remains the source of truth

The provider receives only bounded, already-authorized evidence. OpenAI's current model documentation lists Responses API and streaming support for GPT-5.4 mini: [GPT-5.4 mini](https://developers.openai.com/api/docs/models/gpt-5.4-mini).

Normal tests inject fake Responses API clients and make no network or paid calls. Live provider tests are explicitly enabled with `RUN_LIVE_AI_TESTS=1` and use a small bounded prompt.
