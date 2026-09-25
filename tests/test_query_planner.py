"""Query-planner tests: the gate, the structured call, and fallback behaviour."""

import json

import pytest

from efds_agent.providers.base import GenerationResult, ProviderError, TaskType
from efds_agent.retrieval.gateway_types import ConversationTurn
from efds_agent.retrieval.query_planner import (
    PlannedQuery,
    is_elliptical,
    looks_multi_part,
    needs_planning,
    plan_query,
)


class FakeProvider:
    name = "fake"
    model = "fake-small"

    def __init__(self, payload: str | None = None, error: Exception | None = None):
        self.payload = payload
        self.error = error
        self.calls: list = []

    async def generate(self, request):
        self.calls.append(request)
        if self.error is not None:
            raise self.error
        return GenerationResult(answer=self.payload or "", model="fake-small")

    async def stream(self, request):
        result = await self.generate(request)
        yield result.answer


def plan_payload(standalone: str, queries: list[str], needs_history: bool = False) -> str:
    return json.dumps({"standalone_question": standalone, "sub_queries": queries, "needs_history": needs_history})


HISTORY = [
    ConversationTurn(role="user", content="How do we invite a speaker?"),
    ConversationTurn(role="assistant", content="You submit the approval form."),
]


# ---------------------------------------------------------------- gate


def test_self_contained_single_turn_question_is_not_rewritten():
    required, reason = needs_planning("What is the process for booking a room?", [])
    assert required is False
    assert reason == "self_contained_single_turn"


def test_elliptical_follow_up_with_history_triggers_planning():
    required, reason = needs_planning("and how long does it take?", HISTORY)
    assert required is True
    assert reason == "elliptical_follow_up"


def test_elliptical_question_without_history_does_not_trigger_planning():
    required, _ = needs_planning("and how long does that take?", [])
    assert required is False


def test_multi_part_question_triggers_planning():
    required, reason = needs_planning(
        "Who approves the budget and what is the deadline for sponsor logos and when do we pay?", []
    )
    assert required is True
    assert reason == "multi_part_question"


def test_short_question_is_not_treated_as_multi_part():
    assert looks_multi_part("fees and deadlines") is False


def test_deictic_long_question_is_not_elliptical():
    assert is_elliptical("Can you explain how the whole sponsorship approval workflow operates here?") is False


# ---------------------------------------------------------------- behaviour


@pytest.mark.asyncio
async def test_gate_skips_the_model_call_entirely_for_simple_questions():
    provider = FakeProvider(plan_payload("ignored", ["ignored"]))
    planned = await plan_query("What are the membership fees?", [], provider)
    assert provider.calls == []  # no spend on a self-contained question
    assert planned.planned is False
    assert planned.reason == "self_contained_single_turn"
    assert planned.search_queries() == ["What are the membership fees?"]


@pytest.mark.asyncio
async def test_planned_rewrite_and_sub_queries_are_used():
    provider = FakeProvider(
        plan_payload(
            "How long does external speaker approval take?",
            ["external speaker approval notice period", "speaker approval timeline"],
        )
    )
    planned = await plan_query("and how long does it take?", HISTORY, provider)
    assert planned.planned is True
    assert planned.rewritten is True
    assert planned.reason == "elliptical_follow_up"
    assert planned.standalone_question == "How long does external speaker approval take?"
    # The rewritten question leads; sub-queries follow; the raw question is kept.
    assert planned.search_queries()[0] == "How long does external speaker approval take?"
    assert "speaker approval timeline" in planned.search_queries()
    assert "and how long does it take?" in planned.search_queries()


@pytest.mark.asyncio
async def test_planning_uses_the_cheap_tier_task_type():
    provider = FakeProvider(plan_payload("Standalone?", ["q"]))
    await plan_query("and what about that?", HISTORY, provider)
    assert provider.calls[0].task_type is TaskType.PLANNING
    assert provider.calls[0].response_schema is not None


@pytest.mark.asyncio
async def test_planner_error_falls_back_to_the_raw_question():
    provider = FakeProvider(error=ProviderError("planner unavailable"))
    planned = await plan_query("and how long does it take?", HISTORY, provider)
    assert planned.planned is False
    assert planned.standalone_question == "and how long does it take?"
    assert planned.reason.endswith("+planner_error")
    assert planned.search_queries() == ["and how long does it take?"]


@pytest.mark.asyncio
async def test_unparsable_plan_falls_back_to_the_raw_question():
    provider = FakeProvider("not json at all")
    planned = await plan_query("and how long does it take?", HISTORY, provider)
    assert planned.planned is False
    assert planned.reason.endswith("+unparsable_plan")


@pytest.mark.asyncio
async def test_sub_queries_are_capped_and_deduplicated():
    provider = FakeProvider(
        plan_payload(
            "Standalone",
            ["one", "one", "two", "three", "four"],
        )
    )
    planned = await plan_query("and how long does it take?", HISTORY, provider, sub_query_limit=3)
    assert planned.sub_queries == ["one", "two", "three"]


@pytest.mark.asyncio
async def test_planning_can_be_disabled():
    provider = FakeProvider(plan_payload("Standalone", ["q"]))
    planned = await plan_query("and how long does it take?", HISTORY, provider, enabled=False)
    assert provider.calls == []
    assert planned.reason == "planning_disabled"


@pytest.mark.asyncio
async def test_missing_sub_queries_fall_back_to_the_standalone_question():
    provider = FakeProvider(
        json.dumps({"standalone_question": "Only standalone", "sub_queries": [], "needs_history": False})
    )
    planned = await plan_query("and how long does it take?", HISTORY, provider)
    assert planned.sub_queries == ["Only standalone"]


def test_search_queries_deduplicates_case_insensitively():
    planned = PlannedQuery(question="Fees", standalone_question="fees", sub_queries=["FEES", "deadline"])
    assert planned.search_queries() == ["fees", "deadline"]
