from efds_agent.citations.models import Citation
from efds_agent.retrieval.base import RetrievalRequest, RetrievalResult
from efds_agent.retrieval.helpers import evidence, term_filter, text
from efds_agent.retrieval.supabase import DataAccessError, SupabaseRestClient
from efds_agent.security.authorization import AuthContext
from efds_agent.security.scopes import can_read_source


class OperationalAdapter:
    source = "operational"

    def __init__(self, client: SupabaseRestClient) -> None: self.client = client

    async def search(self, request: RetrievalRequest, auth: AuthContext) -> RetrievalResult:
        if not can_read_source(auth.scope.effective_scope, self.source): return RetrievalResult(source=self.source, queried=False, note="operational records are not readable at this scope")
        found = []
        term = term_filter(request.terms, ("decision_text",))
        try:
            decisions = await self.client.select("decisions", "id,decision_text,approved,decided_at,meeting_id,created_at", [("limit", str(request.max_results)), *( [("or", term)] if term else [] )])
            for row in decisions:
                citation = Citation(id="pending", source_type="decision", source_id=text(row.get("id")), title="EFDS decision", timestamp=row.get("decided_at"), authority="reviewed_operational_record")
                item = evidence(citation, text(row.get("decision_text")), 0.8, {"approved": row.get("approved"), "meeting_id": row.get("meeting_id")})
                if item: found.append(item)
            action_term = term_filter(request.terms, ("title", "description", "status"))
            actions = await self.client.select("action_items", "id,title,description,due_date,status,priority,meeting_id", [("limit", str(request.max_results)), *( [("or", action_term)] if action_term else [] )])
            for row in actions:
                citation = Citation(id="pending", source_type="action_item", source_id=text(row.get("id")), title="EFDS action item", authority="reviewed_operational_record")
                item = evidence(citation, text(row.get("title")) + (": " + text(row.get("description")) if row.get("description") else ""), 0.7, {"status": row.get("status"), "due_date": row.get("due_date")})
                if item: found.append(item)
            meeting_term = term_filter(request.terms, ("title", "meeting_type"))
            meetings = await self.client.select("meetings", "id,title,meeting_type,meeting_date,minutes_document_id", [("limit", str(request.max_results)), *([("or", meeting_term)] if meeting_term else [])])
            for row in meetings:
                citation = Citation(id="pending", source_type="meeting", source_id=text(row.get("id")), title=text(row.get("title"), "EFDS meeting"), timestamp=row.get("meeting_date"), authority="reviewed_operational_record")
                item = evidence(citation, text(row.get("title")) + (" (" + text(row.get("meeting_type")) + ")" if row.get("meeting_type") else ""), 0.65, {"minutes_document_id": row.get("minutes_document_id")})
                if item: found.append(item)
        except DataAccessError:
            # Empty/unavailable operational tables are an absence of evidence.
            pass
        return RetrievalResult(source=self.source, evidence=found[:request.max_results])
