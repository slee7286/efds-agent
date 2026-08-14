"""Deprecated intent router retained only for compatibility tooling.

Primary QA does not classify intents or select source adapters; it calls the
canonical retrieval RPC once through KnowledgeRetrievalGateway.
"""

import re

from pydantic import BaseModel, Field

from efds_agent.security.authorization import AuthContext
from efds_agent.security.scopes import AgentScope, can_read_source


class QueryPlan(BaseModel):
    intents: list[str] = Field(default_factory=list)
    sources: list[str] = Field(default_factory=list)
    filters: dict[str, list[str] | str] = Field(default_factory=dict)
    max_results: int = 8


_PROCESS = ("how do i", "how can i", "what do i need to do", "process", "arrange", "organise", "organize", "steps", "procedure")
_REQUIREMENT = ("need to", "required", "requirement", "must", "approval", "rule", "rules")
_TIMING = ("when", "deadline", "how long", "notice", "before", "timing", "days")
_RESOURCE = ("form", "link", "resource", "where can", "contact", "email")
_SLACK = ("discuss", "discussion", "slack", "what did we", "conversation", "mentioned")
_DOCUMENT = ("document", "file", "operating plan", "handbook", "minutes")
_OPERATIONAL = ("action", "overdue", "decision", "meeting", "treasurer responsible")


def _terms(question: str) -> list[str]:
    words = re.findall(r"[A-Za-z0-9][A-Za-z0-9_-]{2,}", question.lower())
    stop = {
        "what", "when", "where", "which", "about", "need", "does", "have", "from", "with", "that", "this", "before",
        "how", "much", "the", "are", "is", "to", "do", "i", "for", "can", "find", "locate", "show", "me", "please",
        "all", "currently", "assigned", "a", "an", "of", "in", "on", "it", "we", "did", "discuss", "slack",
    }
    return list(dict.fromkeys(word for word in words if word not in stop))[:8]


def build_plan(question: str, auth: AuthContext, max_results: int = 8) -> QueryPlan:
    q, intents = question.lower(), []
    if any(x in q for x in _PROCESS): intents.append("process_lookup")
    if any(x in q for x in _REQUIREMENT): intents.append("requirement_lookup")
    if any(x in q for x in _TIMING): intents.append("timing_lookup")
    if any(x in q for x in _RESOURCE): intents.append("resource_lookup")
    if any(x in q for x in _SLACK): intents.append("discussion_lookup")
    if any(x in q for x in _DOCUMENT): intents.append("document_lookup")
    if any(x in q for x in _OPERATIONAL): intents.append("operational_lookup")
    if not intents: intents.append("general_lookup")
    if auth.scope.effective_scope is AgentScope.PUBLIC:
        sources = ["knowledge_public"]
    else:
        sources = []
        if any(x in intents for x in ("process_lookup", "requirement_lookup", "timing_lookup", "resource_lookup", "general_lookup")): sources.append("knowledge")
        if "discussion_lookup" in intents and can_read_source(auth.scope.effective_scope, "slack"):
            sources.append("slack")
        if "document_lookup" in intents:
            if can_read_source(auth.scope.effective_scope, "documents_onedrive"): sources.append("documents_onedrive")
            elif can_read_source(auth.scope.effective_scope, "documents_legacy"): sources.append("documents_legacy")
            else: pass
        if "operational_lookup" in intents:
            sources.append("operational")
        if not sources and not any(intent in intents for intent in ("discussion_lookup", "document_lookup")):
            sources = ["knowledge"]
    return QueryPlan(intents=intents, sources=sources, filters={"terms": _terms(question)}, max_results=max_results)
