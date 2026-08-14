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


class TokenUsage(BaseModel):
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0


class GenerationRequest(BaseModel):
    question: str
    system_prompt: str
    context: str
    citations: list[Citation] = Field(default_factory=list)
    task_type: TaskType = TaskType.SYNTHESIS


class GenerationResult(BaseModel):
    answer: str
    model: str
    usage: TokenUsage = Field(default_factory=TokenUsage)


class ModelProvider(Protocol):
    name: str
    model: str

    async def generate(self, request: GenerationRequest) -> GenerationResult: ...
    def stream(self, request: GenerationRequest) -> AsyncIterator[str]: ...


def choose_model(settings: object, task_type: TaskType) -> str:
    """Select a configured model without delegating model selection to an LLM."""
    return settings.reasoning_model if task_type is TaskType.REASONING else settings.default_model
