from pydantic import BaseModel, Field

from efds_agent.agent.response import AgentResponse
from efds_agent.retrieval.modes import SourceMode
from efds_agent.security.scopes import AgentScope


class QueryRequest(BaseModel):
    query: str = Field(min_length=1, max_length=1000)
    scope: AgentScope | None = None
    source_mode: SourceMode = SourceMode.PRETERM_KNOWLEDGE
    conversation: list[dict[str, str]] = Field(default_factory=list, max_length=4)


class HealthResponse(BaseModel):
    status: str
    service: str
    version: str
    retrieval: str = "configured"
    model: str = "configured"
    modes: dict[str, str] = Field(default_factory=dict)
    retrieval_contract: dict[str, object] = Field(default_factory=dict)


class RetrievalEvidenceResponse(BaseModel):
    id: str
    retrieval_unit_id: str
    source_type: str
    source_record_id: str
    title: str
    authority: str
    score: float
    visibility: str | None = None
    is_current: bool = True
    is_stale: bool = False
    provenance_preview: str = ""


class RetrievalResponse(BaseModel):
    request_id: str
    query: str
    scope: AgentScope
    source_mode: str
    retrieval_quality: str
    result_count: int
    retrieval_metadata: dict[str, object] = Field(default_factory=dict)
    evidence: list[RetrievalEvidenceResponse] = Field(default_factory=list)


class QueryResponse(AgentResponse):
    pass
