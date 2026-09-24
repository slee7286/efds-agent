"""The sole agent-facing retrieval boundary.

Ranking, semantic retrieval, RLS, provenance and ContextPackage source
semantics remain owned by efds-knowledge-base. This module performs only fixed
RPC invocation, source-mode narrowing, and DTO mapping.
"""

from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel, Field

from efds_agent.agent.context import ContextPackage, build_context
from efds_agent.citations.models import Citation, Evidence
from efds_agent.config import Settings
from efds_agent.retrieval.contract import validate_result_rows
from efds_agent.retrieval.helpers import redact_absolute_paths, safe_relative_path
from efds_agent.retrieval.modes import SOURCE_MODE_TYPES, SourceMode, mode_certification
from efds_agent.retrieval.supabase import DataAccessError, SupabaseRestClient
from efds_agent.security.authorization import AuthContext
from efds_agent.security.scopes import AgentScope


class RetrievalDependencyError(RuntimeError):
    pass


class RetrievalRequest(BaseModel):
    query: str = Field(min_length=1, max_length=4000)
    scope: str
    source_mode: SourceMode = SourceMode.PRETERM_KNOWLEDGE
    limit: int = 10
    conversation_context: str = ""


def bounded_follow_up_query(question: str, turns: list[dict[str, str]] | None, settings: Settings) -> str:
    """Resolve a short follow-up without retaining an unbounded transcript."""
    current = question.strip()
    if not turns:
        return current
    selected: list[str] = []
    used = 0
    for turn in turns[-settings.max_conversation_turns:]:
        role, content = str(turn.get("role", "")), str(turn.get("content", "")).strip()
        if role not in {"user", "assistant"} or not content:
            continue
        remaining = max(settings.max_conversation_chars - used, 0)
        if not remaining:
            break
        value = content[:remaining]
        selected.append(f"{role}: {value}")
        used += len(value)
    return f"Earlier bounded conversation:\n{chr(10).join(selected)}\n\nCurrent question: {current}" if selected else current


def _clean_snippet(value: object) -> str:
    text = str(value or "").replace("<mark>", "").replace("</mark>", "").strip()
    return redact_absolute_paths(re.sub(r"\s+", " ", text))


def _safe_metadata(value: object) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    output: dict[str, Any] = {}
    for key, item in value.items():
        if isinstance(item, str):
            output[str(key)] = redact_absolute_paths(item)
        elif isinstance(item, dict):
            output[str(key)] = _safe_metadata(item)
        elif isinstance(item, list):
            output[str(key)] = [
                redact_absolute_paths(entry) if isinstance(entry, str)
                else _safe_metadata(entry) if isinstance(entry, dict)
                else entry
                for entry in item
            ]
        else:
            output[str(key)] = item
    return output


def _row_to_evidence(row: dict[str, Any], index: int) -> Evidence | None:
    unit_id = str(row.get("retrieval_unit_id") or row.get("id") or "").strip()
    if not unit_id:
        return None
    text = _clean_snippet(row.get("snippet"))
    if not text:
        return None
    metadata = _safe_metadata(row.get("metadata"))
    authority = str(row["authority"])
    citation = Citation(
        id=f"S{index}", retrieval_unit_id=unit_id,
        source_type=str(row.get("source_type") or "unknown"),
        source_id=str(row.get("source_record_id") or unit_id),
        source_record_id=str(row.get("source_record_id") or unit_id),
        source_version_id=row.get("source_version_id"),
        title=str(row.get("title") or "EFDS source"), excerpt=text,
        url=row.get("source_url") if str(row.get("source_url") or "").startswith(("http://", "https://")) else None,
        path=safe_relative_path(row.get("relative_path")),
        channel=row.get("channel"), timestamp=row.get("occurred_at"),
        source_updated_at=row.get("source_updated_at"), content_hash=row.get("content_hash"),
        review_status=row.get("review_status"), authority=authority,
        route=_safe_route(row), metadata={**metadata, "visibility": row.get("visibility")},
    )
    return Evidence(citation=citation, text=text, relevance=float(row.get("score") or 0),
                    freshness=1.0 if row.get("is_current", True) else 0.0,
                    review_status=row.get("review_status"), metadata={**metadata, "canonical_rank": index,
                    "retrieval_unit_id": unit_id, "is_current": bool(row.get("is_current", True)),
                    "is_stale": bool(row.get("is_stale", False)), "authority": authority,
                    "visibility": row.get("visibility")})


def _safe_route(row: dict[str, Any]) -> str | None:
    source_type = str(row.get("source_type") or "")
    if source_type == "document":
        return "/admin/documents"
    if source_type == "slack_message":
        if row.get("visibility") == "committee" and row.get("authority") == "committee_slack":
            source_id = str(row.get("source_record_id") or "")
            if re.fullmatch(r"[0-9a-fA-F-]{36}", source_id):
                return f"/dashboard/slack/messages/{source_id}"
        return "/admin/slack"
    if source_type.startswith("meeting_"):
        return "/admin/meetings"
    if source_type.startswith("operational_"):
        return "/admin/operations"
    return None


class KnowledgeRetrievalGateway:
    """Fixed Supabase/PostgREST retrieval gateway used by primary QA."""

    RPC_NAME = "search_retrieval_units_v1"
    CONTRACT_VERSION = "1"

    def __init__(self, settings: Settings, client: SupabaseRestClient | None = None) -> None:
        self.settings = settings
        self.client = client

    async def retrieve(self, request: RetrievalRequest, auth: AuthContext) -> ContextPackage:
        limit = min(max(request.limit, 1), self.settings.max_retrieval_k, 50)
        mode = request.source_mode
        # Ask the canonical RPC for a bounded candidate window. Source-mode
        # narrowing below never re-ranks or expands access.
        rpc_limit = min(max(limit, 20), 50)
        payload = {
            "search_query": request.query,
            "requested_source_types": list(SOURCE_MODE_TYPES[mode]),
            "requested_area": None,
            "requested_topic": None,
            "requested_channel": None,
            "requested_author": None,
            "requested_from": None,
            "requested_to": None,
            "include_history": False,
            "result_limit": rpc_limit,
            "result_offset": 0,
        }
        rpc_name = self.RPC_NAME
        if mode is SourceMode.COMMITTEE_TICKETS:
            if auth.scope.effective_scope not in {AgentScope.COMMITTEE, AgentScope.ADMIN}:
                raise RetrievalDependencyError("committee ticket evidence requires committee scope")
            rpc_name = "committee_ticket_slack_evidence_v1"
            payload = {"result_limit": min(rpc_limit, 12)}
        elif mode is SourceMode.ADMIN_OUTLOOK_TICKETS:
            if auth.scope.effective_scope is not AgentScope.ADMIN:
                raise RetrievalDependencyError("Outlook ticket evidence requires admin scope")
            rpc_name = "admin_outlook_ticket_evidence_v1"
            payload = {"result_limit": min(rpc_limit, 12)}
        client = self.client or SupabaseRestClient(self.settings, auth.bearer_token)
        try:
            rpc = client.rpc
            rows = await rpc(rpc_name, payload)
            validate_result_rows(rows)
        except (AttributeError, DataAccessError) as exc:
            raise RetrievalDependencyError("canonical retrieval is unavailable") from exc
        except ValueError as exc:
            raise RetrievalDependencyError("canonical retrieval contract is incompatible") from exc
        narrowed: list[dict[str, Any]] = []
        effective_scope = auth.scope.effective_scope
        visibility_ceiling = {
            AgentScope.PUBLIC: 0, AgentScope.VIEWER: 0, AgentScope.MEMBER: 1,
            AgentScope.COMMITTEE: 2, AgentScope.ADMIN: 3,
        }[effective_scope]
        visibility_rank = {"public": 0, "member": 1, "committee": 2, "internal": 3}
        admin_only_types = {"document", "slack_message", "outlook_message", "meeting_transcript", "meeting_summary", "meeting_notes"}
        for row in rows:
            source_type = str(row.get("source_type") or "")
            if source_type not in SOURCE_MODE_TYPES[mode]:
                continue
            if mode is SourceMode.COMMITTEE_TICKETS:
                if (source_type != "slack_message" or row.get("authority") != "committee_slack"
                    or row.get("visibility") != "committee" or row.get("review_status") != "source_generated"):
                    continue
            elif mode is SourceMode.ADMIN_OUTLOOK_TICKETS:
                if (source_type != "outlook_message" or row.get("authority") != "outlook_mail"
                    or row.get("visibility") != "internal" or row.get("review_status") != "source_generated"):
                    continue
            elif source_type in admin_only_types and effective_scope is not AgentScope.ADMIN:
                continue
            if visibility_rank.get(str(row.get("visibility") or "internal"), 3) > visibility_ceiling:
                continue
            if mode is SourceMode.PRETERM_KNOWLEDGE and source_type == "document" and str(row.get("source_area") or "").casefold() != "01_governance":
                continue
            narrowed.append(row)
            if len(narrowed) >= limit:
                break
        evidence: list[Evidence] = []
        for index, row in enumerate(narrowed, start=1):
            item = _row_to_evidence(row, index)
            if item is not None:
                evidence.append(item)
        scores = [float(row.get("score") or 0) for row in narrowed]
        metadata = {
            "retrieval_contract_version": self.CONTRACT_VERSION,
            "retrieval_rpc": rpc_name,
            "retrieval_strategy": "canonical_backend_order",
            "retrieval_quality": "low" if not evidence else ("limited" if len(evidence) == 1 else "normal"),
            "result_count": len(evidence),
            "source_family_count": len({item.citation.source_type for item in evidence}),
            "top_score": scores[0] if scores else None,
            "rank_gap": scores[0] - scores[1] if len(scores) > 1 else None,
            "exact_lexical_match": any(row.get("lexical_rank") == 1 for row in narrowed),
            "source_mode": mode.value,
            "source_mode_certification": mode_certification(mode),
            "candidate_count": len(rows),
            "source_mode_filtered_count": max(len(rows) - len(narrowed), 0),
        }
        return build_context(request.query, evidence, auth.scope.effective_scope,
                             max_items=self.settings.max_context_items,
                             max_chars=max(1000, (self.settings.max_context_tokens - 512) * 4), metadata=metadata)
