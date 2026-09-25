import pytest

from efds_agent.agent.orchestrator import AgentOrchestrator
from efds_agent.config import Settings
from efds_agent.providers.base import GenerationRequest, GenerationResult
from efds_agent.providers.openai import OpenAIProvider
from efds_agent.security.authorization import development_context
from efds_agent.security.scopes import AgentScope


class BadCitationProvider:
    name = "test"
    model = "test"

    async def generate(self, request: GenerationRequest) -> GenerationResult:
        return GenerationResult(answer="Unsupported [S99]", model=self.model)

    async def stream(self, request: GenerationRequest):
        yield "Unsupported [S99]"


class CountingProvider(BadCitationProvider):
    def __init__(self):
        self.calls = 0

    async def generate(self, request: GenerationRequest) -> GenerationResult:
        self.calls += 1
        return await super().generate(request)


class BrokenDataClient:
    async def select(self, table, fields, params=()):
        raise RuntimeError("simulated retrieval failure")


@pytest.mark.asyncio
async def test_orchestrator_has_no_evidence_without_database_and_removes_fake_citations():
    result = await AgentOrchestrator(Settings(_env_file=None), BadCitationProvider()).answer(
        "What is a fictional policy?", development_context(AgentScope.COMMITTEE)
    )
    assert result.citations == []
    assert "S99" not in result.answer


@pytest.mark.asyncio
async def test_stream_emits_safe_sse_contract_events():
    events = [
        event
        async for event in AgentOrchestrator(Settings(_env_file=None), BadCitationProvider()).stream(
            "What is a fictional policy?", development_context(AgentScope.PUBLIC)
        )
    ]
    assert [event["event"] for event in events] == ["meta", "citations", "done"]
    assert "S99" not in events[-1]["answer"]


@pytest.mark.asyncio
async def test_empty_evidence_does_not_invoke_model():
    provider = CountingProvider()
    result = await AgentOrchestrator(Settings(_env_file=None), provider).answer(
        "What is a fictional policy?", development_context(AgentScope.PUBLIC)
    )
    assert provider.calls == 0
    assert result.trace.provider == "none"
    assert "authorized EFDS evidence" in result.answer


@pytest.mark.asyncio
async def test_empty_evidence_does_not_invoke_openai_client():
    class FailingResponses:
        calls = 0

        async def create(self, **kwargs):
            self.calls += 1
            raise AssertionError("OpenAI must not be called without evidence")

    responses = FailingResponses()
    provider = OpenAIProvider(
        Settings(_env_file=None, ai_api_key="test-key"), type("Client", (), {"responses": responses})()
    )
    result = await AgentOrchestrator(Settings(_env_file=None), provider).answer(
        "What is a fictional policy?", development_context(AgentScope.PUBLIC)
    )
    assert responses.calls == 0
    assert result.trace.provider == "none"


@pytest.mark.asyncio
async def test_stream_terminates_safely_on_retrieval_failure():
    events = [
        event
        async for event in AgentOrchestrator(
            Settings(_env_file=None), BadCitationProvider(), BrokenDataClient()
        ).stream("What is EFDS?", development_context(AgentScope.PUBLIC))
    ]
    assert [event["event"] for event in events] == ["error", "done"]
    assert "retrieval" in events[0]["error"]
    assert "authorized EFDS evidence" in events[-1]["answer"]
