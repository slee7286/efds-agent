import logging
import time
from collections.abc import AsyncIterator

from efds_agent.agent.context import ContextPackage
from efds_agent.agent.prompts import system_prompt
from efds_agent.agent.response import AgentResponse, TraceMetadata
from efds_agent.citations.formatter import validate_answer
from efds_agent.citations.verification import VerificationOutcome, verify_citations
from efds_agent.config import Settings
from efds_agent.observability.tracing import RequestTimer, request_id
from efds_agent.providers.base import GenerationRequest, ModelProvider, ProviderError, TaskType, TokenUsage
from efds_agent.providers.openai import OpenAIProvider
from efds_agent.retrieval.base import RetrievalResult
from efds_agent.retrieval.gateway import (
    KnowledgeRetrievalGateway,
    RetrievalDependencyError,
    RetrievalRequest,
)
from efds_agent.retrieval.gateway_types import ConversationTurn
from efds_agent.retrieval.modes import SourceMode
from efds_agent.retrieval.query_planner import plan_query
from efds_agent.retrieval.router import QueryPlan
from efds_agent.security.authorization import AuthContext

logger = logging.getLogger(__name__)

NO_EVIDENCE_ANSWER = "I couldn't find enough authorized EFDS evidence to answer that reliably."
RETRIEVAL_FAILURE_ANSWER = "I couldn't retrieve authorized EFDS evidence right now. Please try again."
MODEL_FAILURE_ANSWER = "I couldn't generate a supported answer right now. Please try again."
UNVERIFIED_ANSWER = (
    "I found EFDS sources for this, but I could not verify my draft answer against the sources it "
    "cited, so I am not stating it as fact. Please rephrase the question, or check the relevant "
    "EFDS source directly."
)


def _history(conversation: list[dict[str, str]] | None, *, max_turns: int, max_chars: int) -> list[ConversationTurn]:
    """Bound prior turns by count and characters, newest turns kept."""
    if not conversation:
        return []
    turns: list[ConversationTurn] = []
    used = 0
    for turn in conversation[-max_turns:]:
        role = str(turn.get("role", ""))
        content = str(turn.get("content", "")).strip()
        if role not in {"user", "assistant"} or not content:
            continue
        remaining = max(max_chars - used, 0)
        if not remaining:
            break
        value = content[:remaining]
        turns.append(ConversationTurn(role=role, content=value))
        used += len(value)
    return turns


class AgentOrchestrator:
    """Single-pass agent orchestration over the canonical retrieval gateway."""

    def __init__(
        self,
        settings: Settings,
        provider: ModelProvider | None = None,
        data_client: object | None = None,
        gateway: KnowledgeRetrievalGateway | None = None,
    ) -> None:
        self.settings = settings
        self.provider = provider or OpenAIProvider(settings)
        # data_client is retained only as a test/development injection point.
        self.gateway = gateway or KnowledgeRetrievalGateway(settings, data_client)  # type: ignore[arg-type]

    @staticmethod
    def _plan(mode: SourceMode, limit: int) -> QueryPlan:
        return QueryPlan(
            intents=["canonical_retrieval"],
            sources=["search_retrieval_units"],
            filters={"source_mode": mode.value},
            max_results=limit,
        )

    @staticmethod
    def _provider_usage(provider: ModelProvider) -> TokenUsage:
        usage = getattr(provider, "last_usage", None)
        return usage if isinstance(usage, TokenUsage) else TokenUsage()

    async def retrieve(
        self,
        question: str,
        auth: AuthContext,
        *,
        source_mode: SourceMode = SourceMode.PRETERM_KNOWLEDGE,
        conversation: list[dict[str, str]] | None = None,
    ) -> tuple[QueryPlan, ContextPackage, list[RetrievalResult]]:
        history = _history(
            conversation, max_turns=self.settings.max_conversation_turns, max_chars=self.settings.max_conversation_chars
        )
        # Query planning is gated: single-turn, self-contained questions are
        # searched as asked, because unconditional rewriting is a measured
        # regression. The planner only runs when the question actually depends
        # on history or bundles several asks, and degrades to the raw question.
        planned = await plan_query(
            question,
            history,
            self.provider,
            enabled=self.settings.enable_query_planning,
            max_turns=self.settings.max_conversation_turns,
            max_history_chars=self.settings.max_conversation_chars,
            sub_query_limit=self.settings.sub_query_limit,
        )
        package = await self.gateway.retrieve(
            RetrievalRequest(
                query=planned.standalone_question,
                scope=auth.scope.effective_scope.value,
                source_mode=source_mode,
                limit=self.settings.retrieval_k,
                sub_queries=planned.sub_queries,
            ),
            auth,
        )
        package.retrieval_metadata.update(
            {
                "query_plan_reason": planned.reason,
                "query_rewritten": planned.rewritten,
                "standalone_question": planned.standalone_question,
            }
        )
        result = RetrievalResult(
            source="canonical_retrieval_rpc",
            evidence=package.items,
            note=None if package.items else "No authorized evidence was returned",
        )
        return self._plan(source_mode, self.settings.retrieval_k), package, [result]

    @staticmethod
    def _trace(
        rid: str,
        auth: AuthContext,
        context: ContextPackage,
        provider: str,
        model: str,
        latency_ms: float,
        phases: dict[str, float],
        *,
        invalid: list[str] | None = None,
        usage: TokenUsage | None = None,
        failure: str | None = None,
        verification: VerificationOutcome | None = None,
    ) -> TraceMetadata:
        metadata = context.retrieval_metadata
        usage = usage or TokenUsage()
        return TraceMetadata(
            request_id=rid,
            scope=auth.scope.effective_scope,
            sources_searched=["search_retrieval_units"],
            result_counts={"canonical_retrieval_rpc": len(context.items)},
            context_items=len(context.items),
            citation_ids=[item.id for item in context.citations],
            provider=provider,
            model=model,
            latency_ms=latency_ms,
            phase_latency_ms=phases,
            invalid_citations_removed=invalid or [],
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            total_tokens=usage.total_tokens,
            cached_tokens=usage.cached_tokens,
            source_mode=str(metadata.get("source_mode", "preterm_knowledge")),
            retrieval_quality=str(metadata.get("retrieval_quality", "low")),
            evidence_chars=int(metadata.get("evidence_chars", 0) or 0),
            dropped_by_budget=int(metadata.get("context_items_dropped_by_budget", 0) or 0),
            failure_category=failure,
            query_plan_reason=str(metadata.get("query_plan_reason", "")),
            query_rewritten=bool(metadata.get("query_rewritten", False)),
            search_query_count=int(metadata.get("search_query_count", 0) or 0),
            retrieval_strategy=str(metadata.get("retrieval_strategy", "")),
            citations_removed_by_verification=list(verification.removed_citations) if verification else [],
            verification_note=verification.note if verification else None,
            answer_withheld_by_verification=bool(verification.withheld) if verification else False,
        )

    def _synthesis_task_type(self, context: ContextPackage) -> TaskType:
        """Pick the synthesis tier from measured question difficulty.

        Escalation is keyed on the planner having rewritten or decomposed the
        question -- the multi-hop cases where a stronger model earns its cost.
        It is bounded because the planner's own gate decides when to fire, so
        an ordinary single-fact lookup stays on the mid tier.
        """
        if not self.settings.escalate_decomposed_questions:
            return TaskType.SYNTHESIS
        metadata = context.retrieval_metadata
        if bool(metadata.get("query_rewritten")) or int(metadata.get("search_query_count") or 0) > 1:
            return TaskType.REASONING
        return TaskType.SYNTHESIS

    async def answer(
        self,
        question: str,
        auth: AuthContext,
        *,
        source_mode: SourceMode = SourceMode.PRETERM_KNOWLEDGE,
        conversation: list[dict[str, str]] | None = None,
    ) -> AgentResponse:
        timer, rid = RequestTimer(), request_id()
        retrieval_started = time.perf_counter()
        plan, context, _ = await self.retrieve(question, auth, source_mode=source_mode, conversation=conversation)
        retrieval_ms = round((time.perf_counter() - retrieval_started) * 1000, 2)
        quality = str(context.retrieval_metadata.get("retrieval_quality", "low"))
        if not context.items:
            trace = self._trace(
                rid,
                auth,
                context,
                "none",
                "",
                timer.latency_ms,
                {"retrieval": retrieval_ms, "model": 0.0},
                failure="insufficient_evidence",
            )
            return AgentResponse(
                request_id=rid,
                answer=NO_EVIDENCE_ANSWER,
                scope=auth.scope.effective_scope,
                citations=[],
                plan=plan,
                limitations=["No authorized evidence was retrieved."],
                trace=trace,
                source_mode=source_mode.value,
                insufficient_evidence=True,
                retrieval_quality=quality,
                source_count=0,
            )
        model_started = time.perf_counter()
        generated = await self.provider.generate(
            GenerationRequest(
                question=question,
                system_prompt=system_prompt(),
                context=context.text,
                citations=context.citations,
                task_type=self._synthesis_task_type(context),
            )
        )
        model_ms = round((time.perf_counter() - model_started) * 1000, 2)
        if not generated.answer.strip():
            raise ProviderError("model returned no answer")
        answer, invalid = validate_answer(generated.answer, context.citations)
        usage = generated.usage
        verification = await verify_citations(
            answer, context.items, context.citations, self.provider, enabled=self.settings.enable_citation_verification
        )
        withheld = verification.withheld
        if withheld:
            answer = UNVERIFIED_ANSWER
        else:
            answer = verification.answer
        limitations = []
        if quality == "limited":
            limitations.append("Only limited authorized EFDS evidence was retrieved; verify important details.")
        if verification.removed_citations:
            limitations.append(
                "Some claims could not be checked against the sources they cited, so those citations were removed."
            )
        if withheld:
            limitations.append("No cited claim could be verified against its source, so the draft answer was withheld.")
        trace = self._trace(
            rid,
            auth,
            context,
            self.provider.name,
            generated.model,
            timer.latency_ms,
            {"retrieval": retrieval_ms, "model": model_ms},
            invalid=invalid,
            usage=usage,
            verification=verification,
        )
        logger.info(
            "agent request",
            extra={
                "trace_data": {
                    "request_id": rid,
                    "scope": auth.scope.effective_scope.value,
                    "query_length": len(question),
                    "result_count": len(context.items),
                    "source_mode": source_mode.value,
                    "citations": [c.id for c in context.citations],
                    "latency_ms": trace.latency_ms,
                }
            },
        )
        return AgentResponse(
            request_id=rid,
            answer=answer,
            scope=auth.scope.effective_scope,
            citations=[] if withheld else context.citations,
            plan=plan,
            limitations=limitations,
            trace=trace,
            source_mode=source_mode.value,
            insufficient_evidence=withheld,
            retrieval_quality=quality,
            source_count=len(context.items),
        )

    async def stream(
        self,
        question: str,
        auth: AuthContext,
        *,
        source_mode: SourceMode = SourceMode.PRETERM_KNOWLEDGE,
        conversation: list[dict[str, str]] | None = None,
    ) -> AsyncIterator[dict[str, object]]:
        timer, rid = RequestTimer(), request_id()
        retrieval_started = time.perf_counter()
        try:
            plan, context, _ = await self.retrieve(question, auth, source_mode=source_mode, conversation=conversation)
        except RetrievalDependencyError:
            logger.exception(
                "canonical retrieval failed",
                extra={"trace_data": {"request_id": rid, "scope": auth.scope.effective_scope.value}},
            )
            yield {
                "event": "error",
                "request_id": rid,
                "error": "retrieval unavailable",
                "category": "retrieval_dependency",
            }
            yield {
                "event": "done",
                "request_id": rid,
                "answer": RETRIEVAL_FAILURE_ANSWER,
                "insufficient_evidence": False,
                "trace": {
                    "request_id": rid,
                    "scope": auth.scope.effective_scope.value,
                    "failure_category": "retrieval_dependency",
                    "latency_ms": timer.latency_ms,
                },
            }
            return
        retrieval_ms = round((time.perf_counter() - retrieval_started) * 1000, 2)
        yield {
            "event": "meta",
            "request_id": rid,
            "effective_scope": auth.scope.effective_scope.value,
            "source_mode": source_mode.value,
            "source_mode_certification": context.retrieval_metadata.get("source_mode_certification"),
            "retrieval_quality": context.retrieval_metadata.get("retrieval_quality", "low"),
            "result_count": len(context.items),
            "source_family_count": context.retrieval_metadata.get("source_family_count", 0),
            "degraded_retrieval": bool(context.retrieval_metadata.get("degraded_retrieval", False)),
            "plan": plan.model_dump(mode="json"),
        }
        if not context.items:
            trace = self._trace(
                rid,
                auth,
                context,
                "none",
                "",
                timer.latency_ms,
                {"retrieval": retrieval_ms, "model": 0.0},
                failure="insufficient_evidence",
            )
            yield {"event": "citations", "citations": [], "insufficient_evidence": True}
            yield {
                "event": "done",
                "request_id": rid,
                "answer": NO_EVIDENCE_ANSWER,
                "insufficient_evidence": True,
                "trace": trace.model_dump(mode="json"),
            }
            return
        generated_chunks: list[str] = []
        request = GenerationRequest(
            question=question,
            system_prompt=system_prompt(),
            context=context.text,
            citations=context.citations,
            task_type=self._synthesis_task_type(context),
        )
        model_started = time.perf_counter()
        try:
            async for chunk in self.provider.stream(request):
                generated_chunks.append(chunk)
                yield {"event": "token", "token": chunk}
        except Exception:
            logger.exception(
                "model stream failed",
                extra={"trace_data": {"request_id": rid, "scope": auth.scope.effective_scope.value}},
            )
            trace = self._trace(
                rid,
                auth,
                context,
                self.provider.name,
                self.provider.model,
                timer.latency_ms,
                {"retrieval": retrieval_ms, "model": round((time.perf_counter() - model_started) * 1000, 2)},
                failure="model_provider",
            )
            yield {
                "event": "error",
                "request_id": rid,
                "error": "model generation failed",
                "category": "model_provider",
            }
            yield {
                "event": "done",
                "request_id": rid,
                "answer": MODEL_FAILURE_ANSWER,
                "trace": trace.model_dump(mode="json"),
            }
            return
        answer, invalid = validate_answer("".join(generated_chunks), context.citations)
        # Capture usage before verification runs, otherwise the verifier's own
        # call overwrites the provider's last_usage and the trace reports the
        # audit's tokens as the answer's.
        usage = self._provider_usage(self.provider)
        verification = await verify_citations(
            answer, context.items, context.citations, self.provider, enabled=self.settings.enable_citation_verification
        )
        # Tokens already streamed cannot be recalled. The `done` event carries
        # the authoritative answer, so a client must render that as final; the
        # correction is also flagged explicitly.
        withheld = verification.withheld
        final_answer = UNVERIFIED_ANSWER if withheld else verification.answer
        trace = self._trace(
            rid,
            auth,
            context,
            self.provider.name,
            self.provider.model,
            timer.latency_ms,
            {"retrieval": retrieval_ms, "model": round((time.perf_counter() - model_started) * 1000, 2)},
            invalid=invalid,
            usage=usage,
            verification=verification,
        )
        yield {
            "event": "citations",
            "citations": [] if withheld else [item.model_dump(mode="json") for item in context.citations],
            "insufficient_evidence": withheld,
            "citations_removed": verification.removed_citations,
            "correction": bool(verification.removed_citations or withheld),
        }
        yield {
            "event": "done",
            "request_id": rid,
            "answer": final_answer,
            "insufficient_evidence": withheld,
            "answer_withheld_by_verification": withheld,
            "trace": trace.model_dump(mode="json"),
        }
