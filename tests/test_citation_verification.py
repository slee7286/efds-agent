"""Citation-verification tests: retraction, refusal, and failure tolerance."""

import json

import pytest

from efds_agent.citations.models import Citation, Evidence
from efds_agent.citations.verification import verify_citations
from efds_agent.providers.base import GenerationResult, ProviderError, TaskType


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


def verdict(unsupported: list[dict[str, str]], all_supported: bool = False) -> str:
    return json.dumps({"unsupported": unsupported, "all_supported": all_supported})


def evidence(*ids: str) -> list[Evidence]:
    items = []
    for index, citation_id in enumerate(ids, start=1):
        citation = Citation(
            id=citation_id,
            source_type="knowledge_requirement",
            source_id=f"r{index}",
            title=f"Source {index}",
            authority="approved_knowledge",
        )
        items.append(Evidence(citation=citation, text=f"Body of source {index}"))
    return items


def citations(*ids: str) -> list[Citation]:
    return [item.citation for item in evidence(*ids)]


ANSWER = "Notice is ten working days [S1]. The form goes to the Treasurer [S2]."


@pytest.mark.asyncio
async def test_all_supported_answer_is_untouched():
    provider = FakeProvider(verdict([], all_supported=True))
    outcome = await verify_citations(ANSWER, evidence("S1", "S2"), citations("S1", "S2"), provider)
    assert outcome.answer == ANSWER
    assert outcome.verified is True
    assert outcome.removed_citations == []
    assert outcome.withheld is False
    assert outcome.checked_citations == 2


@pytest.mark.asyncio
async def test_unsupported_citation_is_removed_and_reported():
    provider = FakeProvider(
        verdict([{"claim": "The form goes to the Treasurer", "citation_id": "S2", "reason": "not stated"}])
    )
    outcome = await verify_citations(ANSWER, evidence("S1", "S2"), citations("S1", "S2"), provider)
    assert "[S2]" not in outcome.answer
    assert "[S1]" in outcome.answer
    assert outcome.removed_citations == ["S2"]
    assert outcome.withheld is False
    assert outcome.unsupported_claims == ["The form goes to the Treasurer"]


@pytest.mark.asyncio
async def test_every_citation_unsupported_withholds_the_answer():
    provider = FakeProvider(
        verdict(
            [
                {"claim": "Notice is ten working days", "citation_id": "S1", "reason": "absent"},
                {"claim": "The form goes to the Treasurer", "citation_id": "S2", "reason": "absent"},
            ]
        )
    )
    outcome = await verify_citations(ANSWER, evidence("S1", "S2"), citations("S1", "S2"), provider)
    assert outcome.withheld is True
    assert outcome.note == "all_citations_unsupported"
    assert outcome.removed_citations == ["S1", "S2"]


@pytest.mark.asyncio
async def test_verdict_about_an_unknown_citation_is_ignored():
    provider = FakeProvider(verdict([{"claim": "invented", "citation_id": "S9", "reason": "n/a"}]))
    outcome = await verify_citations(ANSWER, evidence("S1", "S2"), citations("S1", "S2"), provider)
    # A verdict the answer does not contain cannot be acted on safely.
    assert outcome.answer == ANSWER
    assert outcome.removed_citations == []


@pytest.mark.asyncio
async def test_verifier_error_returns_the_answer_unverified():
    provider = FakeProvider(error=ProviderError("verifier down"))
    outcome = await verify_citations(ANSWER, evidence("S1", "S2"), citations("S1", "S2"), provider)
    assert outcome.answer == ANSWER
    assert outcome.verified is False
    assert outcome.note == "verifier_error"


@pytest.mark.asyncio
async def test_unparsable_verdict_returns_the_answer_unverified():
    provider = FakeProvider("definitely not json")
    outcome = await verify_citations(ANSWER, evidence("S1", "S2"), citations("S1", "S2"), provider)
    assert outcome.answer == ANSWER
    assert outcome.note == "unparsable_verdict"


@pytest.mark.asyncio
async def test_answer_without_citations_is_not_sent_to_the_verifier():
    provider = FakeProvider(verdict([]))
    outcome = await verify_citations("No citations here.", evidence("S1"), citations("S1"), provider)
    assert provider.calls == []
    assert outcome.note == "no_resolvable_citations"


@pytest.mark.asyncio
async def test_empty_citation_set_is_not_sent_to_the_verifier():
    provider = FakeProvider(verdict([]))
    outcome = await verify_citations(ANSWER, [], [], provider)
    assert provider.calls == []
    assert outcome.note == "nothing_to_verify"


@pytest.mark.asyncio
async def test_citation_that_cannot_be_resolved_is_not_verified():
    provider = FakeProvider(verdict([]))
    outcome = await verify_citations(ANSWER, evidence("S1"), citations("S1"), provider)
    # S2 is cited by the answer but has no evidence span, so there is nothing
    # to check it against and no verdict is requested for it.
    assert outcome.checked_citations == 1


@pytest.mark.asyncio
async def test_verification_uses_the_cheap_tier_task_type():
    provider = FakeProvider(verdict([], all_supported=True))
    await verify_citations(ANSWER, evidence("S1", "S2"), citations("S1", "S2"), provider)
    assert provider.calls[0].task_type is TaskType.VERIFICATION
    assert provider.calls[0].response_schema is not None


@pytest.mark.asyncio
async def test_verification_can_be_disabled():
    provider = FakeProvider(verdict([]))
    outcome = await verify_citations(ANSWER, evidence("S1", "S2"), citations("S1", "S2"), provider, enabled=False)
    assert provider.calls == []
    assert outcome.answer == ANSWER
    assert outcome.note == "verification_disabled"
