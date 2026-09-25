"""OpenAI Responses API adapter used by the EFDS agent."""

from collections.abc import AsyncIterator
from typing import Any

from openai import (
    APIConnectionError,
    APIError,
    APITimeoutError,
    AsyncOpenAI,
    AuthenticationError,
    BadRequestError,
    RateLimitError,
)

from efds_agent.config import Settings
from efds_agent.observability.usage import ProcessTokenBudget
from efds_agent.providers.base import (
    GenerationRequest,
    GenerationResult,
    ProviderError,
    TaskType,
    TokenUsage,
    choose_model,
)


class OpenAIProvider:
    name = "openai"

    def __init__(self, settings: Settings, client: Any | None = None) -> None:
        self.settings = settings
        self._client = client
        self.model = settings.default_model
        self.last_usage = TokenUsage()
        self._budget = ProcessTokenBudget(settings.openai_daily_token_budget)

    def _client_instance(self) -> Any:
        if self._client is not None:
            return self._client
        if not self.settings.ai_api_key:
            raise ProviderError("AI_API_KEY is required for the OpenAI provider")
        self._client = AsyncOpenAI(
            api_key=self.settings.ai_api_key.get_secret_value(),
            timeout=self.settings.request_timeout_seconds,
            max_retries=0,
        )
        return self._client

    def _model_for(self, request: GenerationRequest) -> str:
        return choose_model(self.settings, request.task_type)

    @staticmethod
    def _build_input(request: GenerationRequest) -> str:
        """Place evidence before the question.

        The cached prefix must stay byte-stable, so nothing that varies per
        request may precede it. Evidence (the large, variable block) comes
        first and the question last, which also puts the operative instruction
        at the end of the input where recency helps compliance.
        """
        if request.context.strip():
            return f"{request.context}\n\nCurrent question:\n{request.question}"
        return request.question

    # Reasoning effort stays minimal everywhere. Planning and verification are
    # short and structured, and synthesis is grounded in supplied evidence
    # rather than requiring multi-step deduction, so extra reasoning tokens buy
    # latency and cost without accuracy here.
    DEFAULT_EFFORT = "low"

    def _request_kwargs(self, request: GenerationRequest) -> dict[str, object]:
        text_options: dict[str, object] = {}
        if request.response_schema is not None:
            text_options["format"] = {
                "type": "json_schema",
                "name": request.structured_output_name or "response",
                "schema": request.response_schema,
                "strict": True,
            }
        if request.task_type in {TaskType.PLANNING, TaskType.VERIFICATION}:
            text_options["verbosity"] = "low"
        kwargs: dict[str, object] = {
            "model": self._model_for(request),
            "instructions": request.system_prompt,
            "input": self._build_input(request),
            "max_output_tokens": request.max_output_tokens or self.settings.max_output_tokens,
            "reasoning": {"effort": request.reasoning_effort or self.DEFAULT_EFFORT},
            "store": False,
            # Reuses the cached policy prefix across every request and across
            # users. Without this key, load spreads over several cache shards
            # and the hit rate falls away.
            "prompt_cache_key": self.settings.prompt_cache_key,
        }
        if text_options:
            kwargs["text"] = text_options
        return kwargs

    @staticmethod
    def _usage(response: Any) -> TokenUsage:
        usage = getattr(response, "usage", None)
        if usage is None:
            return TokenUsage()
        input_tokens = int(getattr(usage, "input_tokens", 0) or 0)
        output_tokens = int(getattr(usage, "output_tokens", 0) or 0)
        total_tokens = int(getattr(usage, "total_tokens", input_tokens + output_tokens) or input_tokens + output_tokens)
        # Cache telemetry: a silently broken cache looks identical to a working
        # one unless cached_tokens is recorded.
        details = getattr(usage, "input_tokens_details", None)
        cached_tokens = int(getattr(details, "cached_tokens", 0) or 0) if details is not None else 0
        return TokenUsage(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=total_tokens,
            cached_tokens=cached_tokens,
        )

    @staticmethod
    def _error(exc: Exception) -> ProviderError:
        if isinstance(exc, AuthenticationError):
            return ProviderError("OpenAI authentication failed")
        if isinstance(exc, RateLimitError):
            return ProviderError("OpenAI rate limit reached; please try again shortly")
        if isinstance(exc, APITimeoutError):
            return ProviderError("OpenAI request timed out")
        if isinstance(exc, APIConnectionError):
            return ProviderError("OpenAI is unreachable")
        if isinstance(exc, BadRequestError):
            return ProviderError("OpenAI rejected the generation request")
        if isinstance(exc, APIError):
            return ProviderError("OpenAI generation failed")
        return ProviderError("OpenAI generation failed")

    def _reserve(self, request: GenerationRequest) -> None:
        estimate = (
            len(request.system_prompt) + len(request.question) + len(request.context)
        ) // 4 + self.settings.max_output_tokens
        if not self._budget.reserve(estimate):
            raise ProviderError("daily OpenAI token budget reached")

    async def generate(self, request: GenerationRequest) -> GenerationResult:
        self._reserve(request)
        try:
            response = await self._client_instance().responses.create(**self._request_kwargs(request))
        except Exception as exc:
            if isinstance(exc, ProviderError):
                raise
            raise self._error(exc) from exc
        answer = str(getattr(response, "output_text", "") or "")
        if not answer.strip():
            raise ProviderError("OpenAI returned no text")
        self.last_usage = self._usage(response)
        return GenerationResult(answer=answer, model=self._model_for(request), usage=self.last_usage)

    async def stream(self, request: GenerationRequest) -> AsyncIterator[str]:
        self._reserve(request)
        chunks: list[str] = []
        try:
            response = await self._client_instance().responses.create(**self._request_kwargs(request), stream=True)
            async for event in response:
                event_type = str(getattr(event, "type", ""))
                if event_type == "response.output_text.delta":
                    delta = str(getattr(event, "delta", "") or "")
                    if delta:
                        chunks.append(delta)
                        yield delta
                elif event_type == "response.completed":
                    self.last_usage = self._usage(getattr(event, "response", None))
        except Exception as exc:
            if isinstance(exc, ProviderError):
                raise
            raise self._error(exc) from exc
        if not "".join(chunks).strip():
            raise ProviderError("OpenAI returned no streamed text")
