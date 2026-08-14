from efds_agent.citations.models import Citation
from efds_agent.retrieval.base import RetrievalRequest, RetrievalResult
from efds_agent.retrieval.helpers import evidence, lexical_relevance, source_article_relevance, term_filter, text
from efds_agent.retrieval.supabase import DataAccessError, SupabaseRestClient
from efds_agent.security.authorization import AuthContext
from efds_agent.security.scopes import can_read_source


def _fetch_limit(request: RetrievalRequest) -> str:
    return str(min(max(request.max_results * 8, 24), 100))


class KnowledgeAdapter:
    source = "knowledge"

    def __init__(self, client: SupabaseRestClient) -> None:
        self.client = client

    async def _select(self, table: str, fields: str, params: list[tuple[str, str]]) -> list[dict[str, object]]:
        try:
            return await self.client.select(table, fields, params)
        except DataAccessError:
            return []

    async def search(self, request: RetrievalRequest, auth: AuthContext) -> RetrievalResult:
        scope = auth.scope.effective_scope
        if not can_read_source(scope, self.source):
            return RetrievalResult(source=self.source, queried=False, note="scope does not permit raw ICU knowledge")

        limit = _fetch_limit(request)
        query = request.question.lower()
        tables: list[tuple[str, str, str, tuple[str, ...]]] = []
        if any(word in query for word in ("need", "required", "requirement", "must", "rule")):
            tables.append(("knowledge_requirements", "requirement", "requirement_text,applies_to,mandatory", ("requirement_text", "evidence_text", "applies_to")))
        if any(word in query for word in ("when", "deadline", "timing", "notice", "days", "before")):
            tables.append(("knowledge_timing_rules", "timing rule", "description,deadline_type,notice_period_value,notice_period_unit,working_days,absolute_date", ("description", "evidence_text", "relative_to_event_type")))
        if not tables and any(word in query for word in ("form", "resource", "where can")):
            tables.append(("knowledge_resources", "resource", "name,resource_type,url,email,system_name,anchor_text,description", ("name", "description", "anchor_text", "system_name")))
        elif not tables and any(word in query for word in ("contact", "email", "who can i ask")):
            tables.append(("knowledge_contacts", "contact", "name,organisation,email,contact_type,url,description", ("name", "description", "organisation", "contact_type")))
        elif any(word in query for word in ("how do", "how can", "process", "steps", "arrange", "organise", "organize", "procedure")) or not tables:
            tables.extend([
                ("knowledge_processes", "process", "name,description", ("name", "description", "evidence_text")),
                ("knowledge_process_steps", "process step", "title,instruction,condition,step_number", ("title", "instruction", "condition", "evidence_text")),
                ("knowledge_resources", "resource", "name,resource_type,url,email,system_name,anchor_text,description", ("name", "description", "anchor_text", "system_name")),
                ("knowledge_contacts", "contact", "name,organisation,email,contact_type,url,description", ("name", "description", "organisation", "contact_type")),
            ])

        found = []
        fields_common = "id,source_article_id,source_url,source_content_hash,source_updated_at,evidence_text,review_status,confidence,extraction_method,is_stale,visibility"
        for table, kind, body_fields, search_fields in tables:
            params = [("is_stale", "eq.false"), ("review_status", "eq.approved"), ("limit", limit)]
            row_query = term_filter(request.terms, search_fields)
            if row_query:
                params.append(("or", row_query))
            fields = f"{fields_common},{body_fields}"
            for row in await self._select(table, fields, params):
                source_id = text(row.get("source_article_id"))
                body = text(row.get("requirement_text") or row.get("description") or row.get("instruction") or row.get("title") or row.get("name"))
                details = "; ".join(
                    f"{key.replace('_', ' ')}: {row[key]}"
                    for key in ("applies_to", "mandatory", "deadline_type", "absolute_date", "notice_period_value", "notice_period_unit", "url", "email")
                    if row.get(key) not in (None, "")
                )
                if details:
                    body += f" ({details})"
                citation = Citation(
                    id="pending", source_type=f"icu_{kind.replace(' ', '_')}", source_id=text(row.get("id"), source_id),
                    title=text(row.get("name") or row.get("title"), kind.title()), url=row.get("source_url"),
                    source_updated_at=row.get("source_updated_at"), content_hash=row.get("source_content_hash"),
                    review_status=text(row.get("review_status")), authority="approved_structured",
                )
                item = evidence(citation, body, lexical_relevance((body, row.get("evidence_text")), request.terms, 0.6), {"kind": kind, "source_article_id": source_id, "authority": "approved_structured"})
                if item:
                    found.append(item)

        if found:
            return RetrievalResult(source=self.source, evidence=sorted(found, key=lambda item: item.relevance, reverse=True)[: request.max_results])

        # The deployed corpus currently contains proposed structured rows. Do
        # not present those rows as approved EFDS truth; use the ICU article as
        # the provenance-first fallback and disclose the review limitation.
        article_params = [("source_type", "eq.icu_freshdesk"), ("limit", limit)]
        article_query = term_filter(request.terms, ("title", "category", "folder", "markdown"))
        if article_query:
            article_params.append(("or", article_query))
        articles = await self._select(
            "knowledge_articles",
            "id,external_id,title,url,markdown,content_hash,source_updated_at,efds_relevance,relevance_review_status,metadata",
            article_params,
        )
        for row in articles:
            body = text(row.get("markdown"))
            review_status = text(row.get("relevance_review_status"), "not_reviewed")
            citation = Citation(
                id="pending", source_type="icu_article", source_id=text(row.get("id") or row.get("external_id")),
                title=text(row.get("title"), "ICU source article"), url=row.get("url"),
                source_updated_at=row.get("source_updated_at"), content_hash=row.get("content_hash"),
                review_status=review_status, authority="icu_source",
            )
            item = evidence(
                citation, body,
                source_article_relevance(row.get("title"), body, request.terms, 0.4) + (0.05 if row.get("efds_relevance") == "critical" else 0),
                {"fallback": True, "relevance": row.get("efds_relevance"), "review_limitation": review_status == "proposed"},
            )
            if item:
                found.append(item)
        note = "No approved current structured ICU record was found; results use current ICU source articles."
        if articles and any(text(row.get("relevance_review_status")) == "proposed" for row in articles):
            note += " EFDS relevance review is proposed for at least one source article."
        return RetrievalResult(source=self.source, evidence=sorted(found, key=lambda item: item.relevance, reverse=True)[: request.max_results], note=note if not found or articles else None)


class PublicKnowledgeAdapter:
    source = "knowledge_public"

    def __init__(self, client: SupabaseRestClient) -> None:
        self.client = client

    async def search(self, request: RetrievalRequest, auth: AuthContext) -> RetrievalResult:
        query = term_filter(request.terms, ("name", "description", "anchor_text", "system_name"))
        params = [("limit", _fetch_limit(request))]
        if query:
            params.append(("or", query))
        try:
            rows = await self.client.select("public_knowledge_resources", "id,name,resource_type,url,system_name,anchor_text,description,source_article_title,source_article_url,published_at", params)
        except DataAccessError:
            rows = []
        found = []
        for row in rows:
            body = text(row.get("description") or row.get("anchor_text") or row.get("name"))
            citation = Citation(id="pending", source_type="icu_public_resource", source_id=text(row.get("id")), title=text(row.get("name"), "Public EFDS resource"), url=row.get("url") or row.get("source_article_url"), authority="published_public")
            item = evidence(citation, body, lexical_relevance((row.get("name"), body), request.terms, 0.6))
            if item:
                found.append(item)
        return RetrievalResult(source=self.source, evidence=sorted(found, key=lambda item: item.relevance, reverse=True)[: request.max_results])
