from efds_agent.citations.models import Citation
from efds_agent.retrieval.base import RetrievalRequest, RetrievalResult
from efds_agent.retrieval.helpers import (
    evidence,
    redact_absolute_paths,
    safe_relative_path,
    source_article_relevance,
    term_filter,
    text,
)
from efds_agent.retrieval.supabase import DataAccessError, SupabaseRestClient
from efds_agent.security.authorization import AuthContext
from efds_agent.security.scopes import AgentScope, can_read_source


class DocumentAdapter:
    source = "documents"

    def __init__(self, client: SupabaseRestClient) -> None: self.client = client

    async def search(self, request: RetrievalRequest, auth: AuthContext) -> RetrievalResult:
        scope = auth.scope.effective_scope
        if scope is AgentScope.ADMIN: source_type = "onedrive_filesystem"
        elif can_read_source(scope, "documents_legacy"): source_type = "legacy"
        else: return RetrievalResult(source=self.source, queried=False, note="documents are not readable at this scope")
        params = [("is_missing", "eq.false"), ("is_unavailable", "eq.false"), ("limit", str(min(max(request.max_results * 8, 24), 100)))]
        if source_type == "onedrive_filesystem": params.append(("source_type", "eq.onedrive_filesystem"))
        else: params.append(("source_type", "not.eq.onedrive_filesystem"))
        query = term_filter(request.terms, ("title", "relative_path", "raw_text", "source_area"))
        if query: params.append(("or", query))
        try:
            rows = await self.client.select("documents", "id,title,source_type,source_url,relative_path,source_area,raw_text,content_hash,filesystem_modified_at,current_version_id,metadata", params)
        except DataAccessError:
            rows = []
        found = []
        for row in rows:
            path = safe_relative_path(row.get("relative_path"))
            body = redact_absolute_paths(text(row.get("raw_text")))
            if row.get("current_version_id") and row.get("source_type") == "onedrive_filesystem":
                try:
                    versions = await self.client.select("document_versions", "raw_text,content_hash,source_modified_at", [("id", f"eq.{row['current_version_id']}"), ("limit", "1")])
                    if versions and text(versions[0].get("raw_text")):
                        body = redact_absolute_paths(text(versions[0].get("raw_text")))
                        row["content_hash"] = versions[0].get("content_hash")
                        row["filesystem_modified_at"] = versions[0].get("source_modified_at")
                except DataAccessError:
                    pass
            if not body:
                body = text(row.get("title"))
            citation = Citation(id="pending", source_type="document", source_id=text(row.get("id")), title=text(row.get("title"), "EFDS document"), url=row.get("source_url"), path=path, source_updated_at=row.get("filesystem_modified_at"), content_hash=row.get("content_hash"), authority="canonical_document")
            item = evidence(citation, body, source_article_relevance(row.get("title"), f"{row.get('relative_path', '')} {row.get('source_area', '')} {body}", request.terms, 0.45), {"source_area": row.get("source_area"), "source_type": row.get("source_type"), "current_version_id": row.get("current_version_id")})
            if item:
                found.append(item)
        return RetrievalResult(source=self.source, evidence=sorted(found, key=lambda item: item.relevance, reverse=True)[: request.max_results])
