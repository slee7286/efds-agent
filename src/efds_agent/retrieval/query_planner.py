"""Adaptive query planning: standalone-question rewriting and decomposition.

Rewriting is *gated*, not unconditional. Blanket prompt-only rewriting is a
documented regression: it substitutes corpus terms away from the query and has
been measured to cost up to 9% nDCG@10 on well-optimised verticals, while
helping elsewhere. The same literature shows unconditional decomposition
degrades multi-hop precision. So the cheap heuristic gate below decides whether
a model call is warranted at all, and answers single-turn, self-contained
questions with no rewrite and no extra spend.

When the gate fires, the rewrite and the decomposition happen in one
structured-output call on the cheap model tier, and any failure falls back to
the original question: retrieval must never be blocked by the planner.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field, replace
from typing import Any

from efds_agent.providers.base import GenerationRequest, ModelProvider, TaskType
from efds_agent.retrieval.gateway_types import ConversationTurn

logger = logging.getLogger(__name__)

# Deictic and follow-up markers that suggest the question depends on prior turns.
_DEICTIC = frozenset(
    {
        "it",
        "its",
        "that",
        "this",
        "these",
        "those",
        "they",
        "them",
        "their",
        "he",
        "she",
        "there",
        "then",
        "above",
        "previous",
        "same",
        "one",
        "ones",
        "another",
        "such",
        "which",
        "who",
    }
)
_FOLLOW_UP_PREFIXES = (
    "and ",
    "also ",
    "what about",
    "how about",
    "why ",
    "when ",
    "who ",
    "which ",
    "where ",
    "then ",
    "so ",
    "but ",
    "ok ",
    "okay ",
)
# Connectors that suggest the question bundles several distinct asks.
_MULTI_PART = (" and ", " also ", " plus ", " as well as ", " and then ", "; ", " and what ")

ELLIPTICAL_MAX_WORDS = 9
MULTI_PART_MIN_WORDS = 8

PLANNER_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "standalone_question": {"type": "string"},
        "sub_queries": {
            "type": "array",
            "items": {"type": "string"},
            "minItems": 1,
            "maxItems": 3,
        },
        "needs_history": {"type": "boolean"},
    },
    "required": ["standalone_question", "sub_queries", "needs_history"],
    "additionalProperties": False,
}

PLANNER_INSTRUCTIONS = """You prepare search queries for a retrieval system over EFDS Society documents.

Rewrite the user's latest question into one standalone question that makes sense with no conversation history: resolve pronouns and references, and fold in only the context actually needed to identify what is being asked about. Preserve the asker's own vocabulary. Do not add facts, expand acronyms you are unsure of, or introduce synonyms that the source documents may not use.

Then produce between one and three short search queries that together cover the question. Each query should be a keyword-oriented phrase suitable for full-text and vector search, not a sentence. If the question is simple, return exactly one query. Split into more only when the question genuinely asks several distinct things.

Set needs_history to true only if the question cannot be interpreted without the earlier turns."""


@dataclass(frozen=True)
class PlannedQuery:
    """The retrieval plan derived from the user's question."""

    question: str
    standalone_question: str
    sub_queries: list[str] = field(default_factory=list)
    rewritten: bool = False
    planned: bool = False
    reason: str = ""

    def search_queries(self) -> list[str]:
        """Return the de-duplicated queries to run, original question included."""
        ordered: list[str] = []
        for candidate in [self.standalone_question, *self.sub_queries, self.question]:
            clean = candidate.strip()
            if clean and clean.lower() not in {item.lower() for item in ordered}:
                ordered.append(clean)
        return ordered


def _words(question: str) -> list[str]:
    return [token.strip(".,!?;:'\"()").lower() for token in question.split() if token.strip()]


def is_elliptical(question: str) -> bool:
    """Whether the question looks dependent on earlier turns."""
    words = _words(question)
    if not words or len(words) > ELLIPTICAL_MAX_WORDS:
        return False
    lowered = question.strip().lower()
    if any(lowered.startswith(prefix) for prefix in _FOLLOW_UP_PREFIXES):
        return True
    return any(word in _DEICTIC for word in words)


def looks_multi_part(question: str) -> bool:
    """Whether the question appears to bundle several distinct asks."""
    if len(_words(question)) < MULTI_PART_MIN_WORDS:
        return False
    lowered = question.lower()
    return any(connector in lowered for connector in _MULTI_PART)


def needs_planning(question: str, history: list[ConversationTurn]) -> tuple[bool, str]:
    """Decide whether a model call is justified. Returns (needed, reason)."""
    if not question.strip():
        return False, "empty_question"
    if history and is_elliptical(question):
        return True, "elliptical_follow_up"
    if looks_multi_part(question):
        return True, "multi_part_question"
    if not history:
        # Single-turn and self-contained: rewriting here is the case the
        # literature shows can reduce retrieval quality, so it is skipped.
        return False, "self_contained_single_turn"
    return False, "self_contained_follow_up"


def _history_block(history: list[ConversationTurn], max_turns: int, max_chars: int) -> str:
    selected: list[str] = []
    used = 0
    for turn in history[-max_turns:]:
        content = turn.content.strip()
        if not content:
            continue
        remaining = max(max_chars - used, 0)
        if not remaining:
            break
        value = content[:remaining]
        selected.append(f"{turn.role}: {value}")
        used += len(value)
    return "\n".join(selected)


def _parse(payload: str, fallback: str, limit: int) -> PlannedQuery | None:
    try:
        data = json.loads(payload)
    except (TypeError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    standalone = str(data.get("standalone_question") or "").strip() or fallback
    raw_queries = data.get("sub_queries")
    queries: list[str] = []
    if isinstance(raw_queries, list):
        for item in raw_queries:
            text = str(item or "").strip()
            if text and text.lower() not in {q.lower() for q in queries}:
                queries.append(text)
    if not queries:
        queries = [standalone]
    return PlannedQuery(
        question=fallback,
        standalone_question=standalone,
        sub_queries=queries[:limit],
        rewritten=standalone != fallback,
        planned=True,
        reason="planned",
    )


async def plan_query(
    question: str,
    history: list[ConversationTurn],
    provider: ModelProvider,
    *,
    enabled: bool = True,
    max_turns: int = 4,
    max_history_chars: int = 4000,
    sub_query_limit: int = 3,
) -> PlannedQuery:
    """Plan retrieval for one question, degrading to the raw question on failure."""
    baseline = PlannedQuery(
        question=question,
        standalone_question=question,
        sub_queries=[question],
        rewritten=False,
        planned=False,
        reason="not_planned",
    )
    if not enabled:
        return replace(baseline, reason="planning_disabled")
    required, reason = needs_planning(question, history)
    if not required:
        return replace(baseline, reason=reason)
    prompt_parts: list[str] = []
    history_block = _history_block(history, max_turns, max_history_chars)
    if history_block:
        prompt_parts.append(f"Conversation so far:\n{history_block}")
    prompt_parts.append(f"Latest question: {question}")
    prompt_parts.append(f"At most {sub_query_limit} search queries may be returned.")
    request = GenerationRequest(
        question="\n\n".join(prompt_parts),
        system_prompt=PLANNER_INSTRUCTIONS,
        context="",
        task_type=TaskType.PLANNING,
        response_schema=PLANNER_SCHEMA,
        structured_output_name="query_plan",
        max_output_tokens=400,
    )
    try:
        generated = await provider.generate(request)
    except Exception:  # noqa: BLE001 - planning is best-effort; it falls back to the raw question
        logger.warning("query planning failed; using the raw question", extra={"trace_data": {"gate_reason": reason}})
        return replace(baseline, reason=f"{reason}+planner_error")
    parsed = _parse(generated.answer, question, max(sub_query_limit, 1))
    if parsed is None:
        return replace(baseline, reason=f"{reason}+unparsable_plan")
    return PlannedQuery(
        question=question,
        standalone_question=parsed.standalone_question,
        sub_queries=parsed.sub_queries,
        rewritten=parsed.rewritten,
        planned=True,
        reason=reason,
    )
