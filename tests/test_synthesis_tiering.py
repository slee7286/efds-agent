"""Synthesis tiering: escalation must follow question difficulty, not caller role."""

from efds_agent.agent.context import ContextPackage
from efds_agent.agent.orchestrator import AgentOrchestrator
from efds_agent.config import Settings
from efds_agent.providers.base import GenerationResult, TaskType
from efds_agent.security.scopes import AgentScope


class StubProvider:
    name = "stub"
    model = "stub"

    async def generate(self, request):
        return GenerationResult(answer="", model="stub")

    async def stream(self, request):
        yield ""


class StubGateway:
    async def retrieve(self, request, auth):  # pragma: no cover - never called here
        raise AssertionError("the gateway is not exercised by tiering tests")


def orchestrator(**overrides) -> AgentOrchestrator:
    return AgentOrchestrator(Settings(_env_file=None, **overrides), provider=StubProvider(), gateway=StubGateway())


def package(**metadata) -> ContextPackage:
    return ContextPackage(question="q", scope=AgentScope.PUBLIC, retrieval_metadata=dict(metadata))


def test_self_contained_lookup_stays_on_the_default_tier():
    context = package(query_rewritten=False, search_query_count=1)
    assert orchestrator()._synthesis_task_type(context) is TaskType.SYNTHESIS


def test_escalation_is_off_by_default_to_cap_cost():
    context = package(query_rewritten=True, search_query_count=3)
    assert orchestrator()._synthesis_task_type(context) is TaskType.SYNTHESIS


def test_rewritten_question_escalates_when_escalation_is_enabled():
    context = package(query_rewritten=True, search_query_count=1)
    assert orchestrator(escalate_decomposed_questions=True)._synthesis_task_type(context) is TaskType.REASONING


def test_decomposed_question_escalates_when_escalation_is_enabled():
    context = package(query_rewritten=False, search_query_count=3)
    assert orchestrator(escalate_decomposed_questions=True)._synthesis_task_type(context) is TaskType.REASONING


def test_missing_metadata_does_not_escalate():
    assert orchestrator(escalate_decomposed_questions=True)._synthesis_task_type(package()) is TaskType.SYNTHESIS


def test_tier_defaults_are_distinct_and_current():
    settings = Settings(_env_file=None)
    # A duplicated tier would make the reasoning knob dead config.
    assert len({settings.default_model, settings.reasoning_model, settings.small_model}) == 3
    assert settings.default_model == "gpt-5.6-terra"
    assert settings.small_model == "gpt-5.6-luna"
    assert settings.reasoning_model == "gpt-6-astra"
