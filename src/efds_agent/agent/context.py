from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from efds_agent.agent.prompts import new_marker
from efds_agent.citations.formatter import context_block, evidence_region
from efds_agent.citations.models import Citation, Evidence
from efds_agent.security.scopes import AgentScope


class ContextPackage(BaseModel):
    """Agent view of the backend-owned, permission-scoped evidence package."""

    question: str
    scope: AgentScope
    items: list[Evidence] = Field(default_factory=list)
    citations: list[Citation] = Field(default_factory=list)
    text: str = ""
    retrieval_metadata: dict[str, Any] = Field(default_factory=dict)


def build_context(
    question: str,
    evidence_items: list[Evidence] | object,
    scope: AgentScope | list[Evidence],
    legacy_scope: AgentScope | None = None,
    max_items: int = 10,
    max_chars: int = 12000,
    *,
    metadata: dict[str, Any] | None = None,
    marker: str | None = None,
) -> ContextPackage:
    """Bound evidence without changing canonical retrieval order.

    The gateway passes rows in the order returned by the knowledge-base RPC.
    The only agent-side selection is deduplication and deterministic budget
    enforcement; authority/current-state ranking is not reimplemented here.
    """
    # Compatibility for the pre-gateway helper signature
    # build_context(question, plan, evidence, scope, ...). It is retained for
    # old callers/tests only; primary QA always takes the canonical order.
    legacy = legacy_scope is not None
    if legacy:
        items = list(scope) if isinstance(scope, list) else []
        effective_scope = legacy_scope
        authority_order = {"approved_structured": 3, "approved_operational": 3, "icu_source": 2}
        items.sort(key=lambda item: (authority_order.get(item.citation.authority, 1), item.relevance), reverse=True)
    else:
        items = list(evidence_items) if isinstance(evidence_items, list) else []
        effective_scope = scope if isinstance(scope, AgentScope) else AgentScope.PUBLIC
    unique: list[Evidence] = []
    seen: set[str] = set()
    for item in items:
        stable_id = item.citation.retrieval_unit_id or f"{item.citation.source_type}:{item.citation.source_id}"
        if stable_id in seen:
            continue
        seen.add(stable_id)
        unique.append(item)
    selected: list[Evidence] = []
    blocks: list[str] = []
    total = 0
    dropped = 0
    # One marker for the whole evidence region. It is unpredictable per request
    # and stripped from source text by the formatter, so retrieved content
    # cannot imitate the boundary of the region it sits inside.
    evidence_marker = marker or new_marker()
    for item in unique[:max_items]:
        block = context_block(item.citation, item.text, evidence_marker)
        if total + len(block) > max_chars:
            dropped += 1
            continue
        selected.append(item)
        blocks.append(block)
        total += len(block)
    dropped += max(len(unique) - min(len(unique), max_items), 0)
    result_metadata = dict(metadata or {})
    result_metadata.update(
        {
            "context_items_available": len(unique),
            "context_items_included": len(selected),
            "context_items_dropped_by_budget": dropped,
            "evidence_chars": total,
            "evidence_marker": evidence_marker,
        }
    )
    citations = [item.citation for item in selected]
    return ContextPackage(
        question=question,
        scope=effective_scope,
        items=selected,
        citations=citations,
        text=evidence_region(blocks, evidence_marker),
        retrieval_metadata=result_metadata,
    )
