from pydantic import BaseModel, Field

from efds_agent.citations.models import Citation
from efds_agent.retrieval.router import QueryPlan
from efds_agent.security.scopes import AgentScope


class TraceMetadata(BaseModel):
    request_id: str
    scope: AgentScope
    sources_searched: list[str] = Field(default_factory=list)
    result_counts: dict[str, int] = Field(default_factory=dict)
    context_items: int = 0
    citation_ids: list[str] = Field(default_factory=list)
    provider: str = ""
    model: str = ""
    latency_ms: float = 0
    phase_latency_ms: dict[str, float] = Field(default_factory=dict)
    invalid_citations_removed: list[str] = Field(default_factory=list)
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    source_mode: str = "preterm_knowledge"
    retrieval_quality: str = "low"
    evidence_chars: int = 0
    dropped_by_budget: int = 0
    failure_category: str | None = None


class AgentResponse(BaseModel):
    request_id: str
    answer: str
    scope: AgentScope
    citations: list[Citation] = Field(default_factory=list)
    plan: QueryPlan
    limitations: list[str] = Field(default_factory=list)
    trace: TraceMetadata
    source_mode: str = "preterm_knowledge"
    insufficient_evidence: bool = False
    retrieval_quality: str = "low"
    source_count: int = 0
