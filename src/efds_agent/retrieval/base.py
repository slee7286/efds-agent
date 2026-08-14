"""Deprecated source-adapter contracts; not used by primary QA."""

from typing import Protocol

from pydantic import BaseModel, Field

from efds_agent.citations.models import Evidence
from efds_agent.security.authorization import AuthContext


class RetrievalRequest(BaseModel):
    question: str
    terms: list[str] = Field(default_factory=list)
    max_results: int = 8
    channel: str | None = None
    author: str | None = None
    date_from: str | None = None
    date_to: str | None = None


class RetrievalResult(BaseModel):
    source: str
    evidence: list[Evidence] = Field(default_factory=list)
    queried: bool = True
    note: str | None = None


class RetrievalAdapter(Protocol):
    source: str
    async def search(self, request: RetrievalRequest, auth: AuthContext) -> RetrievalResult: ...
