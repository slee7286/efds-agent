"""Deprecated compatibility types; primary V1 QA has no model tool registry.

The V1 orchestrator calls the fixed KnowledgeRetrievalGateway directly. These
types remain only for compatibility and do not expose SQL or write operations.
"""

from typing import Protocol

from pydantic import BaseModel, Field

from efds_agent.citations.models import Evidence
from efds_agent.security.authorization import AuthContext


class ToolInput(BaseModel):
    query: str = Field(min_length=1, max_length=1000)
    limit: int = Field(default=8, ge=1, le=20)


class ToolOutput(BaseModel):
    tool_name: str
    evidence: list[Evidence] = Field(default_factory=list)
    note: str | None = None


class ReadOnlyTool(Protocol):
    name: str

    async def run(self, input: ToolInput, auth: AuthContext) -> ToolOutput: ...
