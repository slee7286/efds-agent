from datetime import datetime

from pydantic import BaseModel, Field


class Citation(BaseModel):
    id: str
    retrieval_unit_id: str = ""
    source_type: str
    source_id: str
    source_record_id: str = ""
    source_version_id: str | None = None
    title: str
    excerpt: str = ""
    url: str | None = None
    path: str | None = None
    channel: str | None = None
    timestamp: datetime | str | None = None
    source_updated_at: datetime | str | None = None
    content_hash: str | None = None
    review_status: str | None = None
    authority: str = "source"
    route: str | None = None
    metadata: dict[str, object] = Field(default_factory=dict)


class Evidence(BaseModel):
    citation: Citation
    text: str = Field(min_length=1)
    relevance: float = 0.5
    freshness: float = 0.5
    review_status: str | None = None
    metadata: dict[str, object] = Field(default_factory=dict)
