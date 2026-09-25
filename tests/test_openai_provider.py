from types import SimpleNamespace

import pytest

from efds_agent.config import Settings
from efds_agent.providers.base import GenerationRequest, ProviderError, TaskType
from efds_agent.providers.openai import OpenAIProvider

SYNTHESIS_MODEL = "gpt-5.6-terra"
SMALL_MODEL = "gpt-5.6-luna"


def request(task_type=TaskType.SYNTHESIS) -> GenerationRequest:
    return GenerationRequest(
        question="What is EFDS?",
        system_prompt="Use evidence only.",
        context="[S1] EFDS is a society.",
        task_type=task_type,
    )


class FakeResponses:
    def __init__(self, response):
        self.response = response
        self.calls = []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        return self.response


class FakeClient:
    def __init__(self, response):
        self.responses = FakeResponses(response)


def usage(input_tokens=12, output_tokens=5, total_tokens=17, cached_tokens=0):
    return SimpleNamespace(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        total_tokens=total_tokens,
        input_tokens_details=SimpleNamespace(cached_tokens=cached_tokens),
    )


@pytest.mark.asyncio
async def test_openai_generate_uses_responses_api_and_records_usage():
    client = FakeClient(SimpleNamespace(output_text="Answer [S1]", usage=usage()))
    provider = OpenAIProvider(Settings(_env_file=None, ai_api_key="test-key"), client)
    result = await provider.generate(request())
    call = client.responses.calls[0]
    assert result.answer == "Answer [S1]"
    assert result.model == SYNTHESIS_MODEL
    assert result.usage.total_tokens == 17
    assert call["model"] == SYNTHESIS_MODEL
    assert call["store"] is False
    assert call["max_output_tokens"] == 700
    assert "test-key" not in str(call)


@pytest.mark.asyncio
async def test_openai_sets_prompt_cache_key_and_keeps_evidence_before_question():
    client = FakeClient(SimpleNamespace(output_text="Answer [S1]", usage=usage()))
    provider = OpenAIProvider(Settings(_env_file=None, ai_api_key="test-key"), client)
    await provider.generate(request())
    call = client.responses.calls[0]
    # The cached prefix is the instructions; the key keeps load on one shard.
    assert call["prompt_cache_key"] == "efds-agent-v2"
    assert call["instructions"] == "Use evidence only."
    # Evidence precedes the question so nothing variable comes first.
    assert call["input"].index("[S1] EFDS is a society.") < call["input"].index("What is EFDS?")


@pytest.mark.asyncio
async def test_openai_records_cached_tokens():
    client = FakeClient(SimpleNamespace(output_text="Answer [S1]", usage=usage(cached_tokens=1024)))
    provider = OpenAIProvider(Settings(_env_file=None, ai_api_key="test-key"), client)
    result = await provider.generate(request())
    assert result.usage.cached_tokens == 1024


@pytest.mark.asyncio
async def test_openai_planning_task_uses_small_model_and_json_schema():
    client = FakeClient(SimpleNamespace(output_text='{"ok": true}', usage=usage()))
    provider = OpenAIProvider(Settings(_env_file=None, ai_api_key="test-key"), client)
    schema = {"type": "object", "properties": {"ok": {"type": "boolean"}}, "required": ["ok"]}
    await provider.generate(
        GenerationRequest(
            question="plan this",
            system_prompt="plan",
            context="",
            task_type=TaskType.PLANNING,
            response_schema=schema,
            structured_output_name="query_plan",
        )
    )
    call = client.responses.calls[0]
    assert call["model"] == SMALL_MODEL
    assert call["text"]["format"]["type"] == "json_schema"
    assert call["text"]["format"]["name"] == "query_plan"
    assert call["text"]["format"]["strict"] is True
    assert call["text"]["verbosity"] == "low"


@pytest.mark.asyncio
async def test_openai_stream_translates_text_deltas_and_records_completion_usage():
    events = [
        SimpleNamespace(type="response.output_text.delta", delta="one "),
        SimpleNamespace(type="response.output_text.delta", delta="two"),
        SimpleNamespace(type="response.completed", response=SimpleNamespace(usage=usage(10, 4, 14))),
    ]

    class StreamClient(FakeClient):
        def __init__(self):
            self.responses = FakeResponses(None)

        async def create_stream(self, **kwargs):
            return self.stream()

    class StreamResponses(FakeResponses):
        async def create(self, **kwargs):
            self.calls.append(kwargs)

            async def events_stream():
                for event in events:
                    yield event

            return events_stream()

    client = FakeClient(None)
    client.responses = StreamResponses(None)
    provider = OpenAIProvider(Settings(_env_file=None, ai_api_key="test-key"), client)
    chunks = [chunk async for chunk in provider.stream(request())]
    assert chunks == ["one ", "two"]
    assert provider.last_usage.total_tokens == 14
    assert client.responses.calls[0]["stream"] is True


@pytest.mark.asyncio
async def test_openai_reasoning_task_uses_reasoning_model():
    client = FakeClient(SimpleNamespace(output_text="Answer [S1]", usage=usage()))
    provider = OpenAIProvider(Settings(_env_file=None, ai_api_key="test-key", reasoning_model="reasoning-test"), client)
    await provider.generate(request(TaskType.REASONING))
    assert client.responses.calls[0]["model"] == "reasoning-test"


@pytest.mark.asyncio
async def test_openai_empty_response_is_safe_provider_error():
    client = FakeClient(SimpleNamespace(output_text="", usage=usage()))
    provider = OpenAIProvider(Settings(_env_file=None, ai_api_key="test-key"), client)
    with pytest.raises(ProviderError, match="no text"):
        await provider.generate(request())


@pytest.mark.asyncio
async def test_openai_request_failure_is_safe_provider_error():
    class FailingResponses:
        async def create(self, **kwargs):
            raise RuntimeError("private SDK detail")

    client = SimpleNamespace(responses=FailingResponses())
    provider = OpenAIProvider(Settings(_env_file=None, ai_api_key="test-key"), client)
    with pytest.raises(ProviderError, match="OpenAI generation failed"):
        await provider.generate(request())
