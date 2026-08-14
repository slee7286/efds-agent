from types import SimpleNamespace

import pytest

from efds_agent.config import Settings
from efds_agent.providers.base import GenerationRequest, ProviderError, TaskType
from efds_agent.providers.openai import OpenAIProvider


def request(task_type=TaskType.SYNTHESIS) -> GenerationRequest:
    return GenerationRequest(question="What is EFDS?", system_prompt="Use evidence only.", context="[S1] EFDS is a society.", task_type=task_type)


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


def usage(input_tokens=12, output_tokens=5, total_tokens=17):
    return SimpleNamespace(input_tokens=input_tokens, output_tokens=output_tokens, total_tokens=total_tokens)


@pytest.mark.asyncio
async def test_openai_generate_uses_responses_api_and_records_usage():
    client = FakeClient(SimpleNamespace(output_text="Answer [S1]", usage=usage()))
    provider = OpenAIProvider(Settings(_env_file=None, ai_api_key="test-key"), client)
    result = await provider.generate(request())
    call = client.responses.calls[0]
    assert result.answer == "Answer [S1]"
    assert result.model == "gpt-5.4-mini"
    assert result.usage.total_tokens == 17
    assert call["model"] == "gpt-5.4-mini"
    assert call["store"] is False
    assert call["max_output_tokens"] == 700
    assert "test-key" not in str(call)


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
