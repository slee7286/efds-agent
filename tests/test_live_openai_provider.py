import os

import pytest

from efds_agent.config import get_settings
from efds_agent.providers.base import GenerationRequest
from efds_agent.providers.openai import OpenAIProvider


@pytest.mark.asyncio
@pytest.mark.skipif(os.getenv("RUN_LIVE_AI_TESTS") != "1", reason="paid live provider tests are opt-in")
async def test_live_openai_provider_small_bounded_request():
    provider = OpenAIProvider(get_settings())
    result = await provider.generate(GenerationRequest(
        question="Answer with exactly one short sentence: what is EFDS?",
        system_prompt="Use only the supplied evidence and do not invent citations.",
        context="[S1] EFDS is the Economics, Finance & Data Science Society at Imperial College London.",
    ))
    assert result.answer
    assert result.model == get_settings().default_model
    assert result.usage.total_tokens > 0


@pytest.mark.asyncio
@pytest.mark.skipif(os.getenv("RUN_LIVE_AI_TESTS") != "1", reason="paid live provider tests are opt-in")
async def test_live_openai_provider_stream_small_bounded_request():
    provider = OpenAIProvider(get_settings())
    chunks = [chunk async for chunk in provider.stream(GenerationRequest(
        question="Answer with exactly one short sentence: what is EFDS?",
        system_prompt="Use only the supplied evidence and do not invent citations.",
        context="[S1] EFDS is the Economics, Finance & Data Science Society at Imperial College London.",
    ))]
    assert "".join(chunks)
    assert provider.last_usage.total_tokens > 0
