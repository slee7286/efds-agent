from collections.abc import AsyncIterator
from enum import StrEnum
from typing import Protocol

from pydantic import BaseModel, Field

from efds_agent.citations.models import Citation


class ProviderError(RuntimeError):
    """Safe model-provider failure without vendor response contents."""


class TaskType(StrEnum):
    SYNTHESIS = "synthesis"
    REASONING = "reasoning"
    PLANNING = "planning"
    VERIFICATION = "verification"


class TokenUsage(BaseModel):
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    # Prompt-cache telemetry. Without cached_tokens there is no way to tell a
    # working cache from a silently broken one, so it is tracked on every call.
    cached_tokens: int = 0


class GenerationRequest(BaseModel):
    question: str
    system_prompt: str
    context: str
    citations: list[Citation] = Field(default_factory=list)
    task_type: TaskType = TaskType.SYNTHESIS
    # When set, the provider must return JSON conforming to this schema.
    response_schema: dict[str, object] | None = None
    structured_output_name: str | None = None
    max_output_tokens: int | None = None
    reasoning_effort: str | None = None


class GenerationResult(BaseModel):
    answer: str
    model: str
    usage: TokenUsage = Field(default_factory=TokenUsage)


class ModelProvider(Protocol):
    name: str
    model: str

    async def generate(self, request: GenerationRequest) -> GenerationResult: ...
    def stream(self, request: GenerationRequest) -> AsyncIterator[str]: ...


class ModelTierSettings(Protocol):
    """Structural view of the model-tier settings, so this module stays
    independent of the concrete Settings class."""

    default_model: str
    reasoning_model: str
    small_model: str


def choose_model(settings: ModelTierSettings, task_type: TaskType) -> str:
    """Select a configured model without delegating model selection to an LLM."""
    if task_type is TaskType.REASONING:
        return settings.reasoning_model
    if task_type in {TaskType.PLANNING, TaskType.VERIFICATION}:
        return settings.small_model
    return settings.default_model
