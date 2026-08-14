import logging
import time
from collections.abc import AsyncIterator

from efds_agent.agent.context import ContextPackage
from efds_agent.agent.prompts import system_prompt
from efds_agent.agent.response import AgentResponse, TraceMetadata
from efds_agent.citations.formatter import validate_answer
from efds_agent.config import Settings
from efds_agent.observability.tracing import RequestTimer, request_id
from efds_agent.providers.base import GenerationRequest, ModelProvider, ProviderError, TokenUsage
from efds_agent.providers.openai import OpenAIProvider
from efds_agent.retrieval.base import RetrievalResult
from efds_agent.retrieval.gateway import (
    KnowledgeRetrievalGateway,
    RetrievalDependencyError,
    RetrievalRequest,
    bounded_follow_up_query,
)
from efds_agent.retrieval.modes import SourceMode
from efds_agent.retrieval.router import QueryPlan
from efds_agent.security.authorization import AuthContext

logger = logging.getLogger(__name__)

NO_EVIDENCE_ANSWER = "I couldn't find enough authorized EFDS evidence to answer that reliably."
RETRIEVAL_FAILURE_ANSWER = "I couldn't retrieve authorized EFDS evidence right now. Please try again."
MODEL_FAILURE_ANSWER = "I couldn't generate a supported answer right now. Please try again."


class AgentOrchestrator:
    """Single-pass agent orchestration over the canonical retrieval gateway."""

    def __init__(self, settings: Settings, provider: ModelProvider | None = None,
                 data_client: object | None = None,
                 gateway: KnowledgeRetrievalGateway | None = None) -> None:
        self.settings = settings
        self.provider = provider or OpenAIProvider(settings)
        # data_client is retained only as a test/development injection point.
        self.gateway = gateway or KnowledgeRetrievalGateway(settings, data_client)  # type: ignore[arg-type]

    @staticmethod
    def _plan(mode: SourceMode, limit: int) -> QueryPlan:
        return QueryPlan(intents=["canonical_retrieval"], sources=["search_retrieval_units"],
                         filters={"source_mode": mode.value}, max_results=limit)

    @staticmethod
    def _provider_usage(provider: ModelProvider) -> TokenUsage:
        usage = getattr(provider, "last_usage", None)
        return usage if isinstance(usage, TokenUsage) else TokenUsage()

    async def retrieve(self, question: str, auth: AuthContext, *, source_mode: SourceMode = SourceMode.PRETERM_KNOWLEDGE,
                       conversation: list[dict[str, str]] | None = None) -> tuple[QueryPlan, ContextPackage, list[RetrievalResult]]:
        query = bounded_follow_up_query(question, conversation, self.settings)
        package = await self.gateway.retrieve(RetrievalRequest(query=query, scope=auth.scope.effective_scope.value,
                                                               source_mode=source_mode, limit=self.settings.retrieval_k), auth)
        result = RetrievalResult(source="canonical_retrieval_rpc", evidence=package.items,
                                 note=None if package.items else "No authorized evidence was returned")
        return self._plan(source_mode, self.settings.retrieval_k), package, [result]

    @staticmethod
    def _trace(rid: str, auth: AuthContext, context: ContextPackage, provider: str, model: str,
               latency_ms: float, phases: dict[str, float], *, invalid: list[str] | None = None,
               usage: TokenUsage | None = None, failure: str | None = None) -> TraceMetadata:
        metadata = context.retrieval_metadata
        usage = usage or TokenUsage()
        return TraceMetadata(
            request_id=rid, scope=auth.scope.effective_scope,
            sources_searched=["search_retrieval_units"],
            result_counts={"canonical_retrieval_rpc": len(context.items)},
            context_items=len(context.items), citation_ids=[item.id for item in context.citations],
            provider=provider, model=model, latency_ms=latency_ms, phase_latency_ms=phases,
            invalid_citations_removed=invalid or [], input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens, total_tokens=usage.total_tokens,
            source_mode=str(metadata.get("source_mode", "preterm_knowledge")),
            retrieval_quality=str(metadata.get("retrieval_quality", "low")),
            evidence_chars=int(metadata.get("evidence_chars", 0) or 0),
            dropped_by_budget=int(metadata.get("context_items_dropped_by_budget", 0) or 0),
            failure_category=failure,
        )

    async def answer(self, question: str, auth: AuthContext, *, source_mode: SourceMode = SourceMode.PRETERM_KNOWLEDGE,
                     conversation: list[dict[str, str]] | None = None) -> AgentResponse:
        timer, rid = RequestTimer(), request_id()
        retrieval_started = time.perf_counter()
        plan, context, _ = await self.retrieve(question, auth, source_mode=source_mode, conversation=conversation)
        retrieval_ms = round((time.perf_counter() - retrieval_started) * 1000, 2)
        quality = str(context.retrieval_metadata.get("retrieval_quality", "low"))
        if not context.items:
            trace = self._trace(rid, auth, context, "none", "", timer.latency_ms,
                                {"retrieval": retrieval_ms, "model": 0.0}, failure="insufficient_evidence")
            return AgentResponse(request_id=rid, answer=NO_EVIDENCE_ANSWER, scope=auth.scope.effective_scope,
                                 citations=[], plan=plan, limitations=["No authorized evidence was retrieved."], trace=trace,
                                 source_mode=source_mode.value, insufficient_evidence=True, retrieval_quality=quality, source_count=0)
        model_started = time.perf_counter()
        generated = await self.provider.generate(GenerationRequest(question=question, system_prompt=system_prompt(),
                                                                      context=context.text, citations=context.citations))
        model_ms = round((time.perf_counter() - model_started) * 1000, 2)
        if not generated.answer.strip():
            raise ProviderError("model returned no answer")
        answer, invalid = validate_answer(generated.answer, context.citations)
        usage = generated.usage
        limitations = []
        if quality == "limited":
            limitations.append("Only limited authorized EFDS evidence was retrieved; verify important details.")
        trace = self._trace(rid, auth, context, self.provider.name, generated.model, timer.latency_ms,
                            {"retrieval": retrieval_ms, "model": model_ms}, invalid=invalid, usage=usage)
        logger.info("agent request", extra={"trace_data": {"request_id": rid, "scope": auth.scope.effective_scope.value,
                                                            "query_length": len(question), "result_count": len(context.items),
                                                            "source_mode": source_mode.value, "citations": [c.id for c in context.citations],
                                                            "latency_ms": trace.latency_ms}})
        return AgentResponse(request_id=rid, answer=answer, scope=auth.scope.effective_scope,
                             citations=context.citations, plan=plan, limitations=limitations, trace=trace,
                             source_mode=source_mode.value, insufficient_evidence=False,
                             retrieval_quality=quality, source_count=len(context.items))

    async def stream(self, question: str, auth: AuthContext, *, source_mode: SourceMode = SourceMode.PRETERM_KNOWLEDGE,
                     conversation: list[dict[str, str]] | None = None) -> AsyncIterator[dict[str, object]]:
        timer, rid = RequestTimer(), request_id()
        retrieval_started = time.perf_counter()
        try:
            plan, context, _ = await self.retrieve(question, auth, source_mode=source_mode, conversation=conversation)
        except RetrievalDependencyError:
            logger.exception("canonical retrieval failed", extra={"trace_data": {"request_id": rid, "scope": auth.scope.effective_scope.value}})
            yield {"event": "error", "request_id": rid, "error": "retrieval unavailable", "category": "retrieval_dependency"}
            yield {"event": "done", "request_id": rid, "answer": RETRIEVAL_FAILURE_ANSWER,
                   "insufficient_evidence": False, "trace": {"request_id": rid, "scope": auth.scope.effective_scope.value,
                   "failure_category": "retrieval_dependency", "latency_ms": timer.latency_ms}}
            return
        retrieval_ms = round((time.perf_counter() - retrieval_started) * 1000, 2)
        yield {"event": "meta", "request_id": rid, "effective_scope": auth.scope.effective_scope.value,
               "source_mode": source_mode.value, "source_mode_certification": context.retrieval_metadata.get("source_mode_certification"),
               "retrieval_quality": context.retrieval_metadata.get("retrieval_quality", "low"),
               "result_count": len(context.items), "source_family_count": context.retrieval_metadata.get("source_family_count", 0),
               "degraded_retrieval": bool(context.retrieval_metadata.get("degraded_retrieval", False)),
               "plan": plan.model_dump(mode="json")}
        if not context.items:
            trace = self._trace(rid, auth, context, "none", "", timer.latency_ms,
                                {"retrieval": retrieval_ms, "model": 0.0}, failure="insufficient_evidence")
            yield {"event": "citations", "citations": [], "insufficient_evidence": True}
            yield {"event": "done", "request_id": rid, "answer": NO_EVIDENCE_ANSWER,
                   "insufficient_evidence": True, "trace": trace.model_dump(mode="json")}
            return
        generated_chunks: list[str] = []
        request = GenerationRequest(question=question, system_prompt=system_prompt(), context=context.text, citations=context.citations)
        model_started = time.perf_counter()
        try:
            async for chunk in self.provider.stream(request):
                generated_chunks.append(chunk)
                yield {"event": "token", "token": chunk}
        except Exception:
            logger.exception("model stream failed", extra={"trace_data": {"request_id": rid, "scope": auth.scope.effective_scope.value}})
            trace = self._trace(rid, auth, context, self.provider.name, self.provider.model, timer.latency_ms,
                                {"retrieval": retrieval_ms, "model": round((time.perf_counter() - model_started) * 1000, 2)}, failure="model_provider")
            yield {"event": "error", "request_id": rid, "error": "model generation failed", "category": "model_provider"}
            yield {"event": "done", "request_id": rid, "answer": MODEL_FAILURE_ANSWER, "trace": trace.model_dump(mode="json")}
            return
        answer, invalid = validate_answer("".join(generated_chunks), context.citations)
        usage = self._provider_usage(self.provider)
        trace = self._trace(rid, auth, context, self.provider.name, self.provider.model, timer.latency_ms,
                            {"retrieval": retrieval_ms, "model": round((time.perf_counter() - model_started) * 1000, 2)},
                            invalid=invalid, usage=usage)
        yield {"event": "citations", "citations": [item.model_dump(mode="json") for item in context.citations],
               "insufficient_evidence": False}
        yield {"event": "done", "request_id": rid, "answer": answer, "insufficient_evidence": False,
               "trace": trace.model_dump(mode="json")}
