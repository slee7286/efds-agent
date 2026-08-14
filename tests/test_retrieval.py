from efds_agent.retrieval.base import RetrievalRequest
from efds_agent.retrieval.documents import DocumentAdapter
from efds_agent.retrieval.helpers import source_article_relevance
from efds_agent.retrieval.knowledge import KnowledgeAdapter
from efds_agent.retrieval.slack import SlackAdapter
from efds_agent.security.authorization import development_context
from efds_agent.security.scopes import AgentScope


def test_source_article_relevance_prefers_title_and_phrase_matches():
    relevant = source_article_relevance("Speaker Events Process", "External speaker process", ["inviting", "external", "speaker"])
    unrelated = source_article_relevance("Alcohol Guidelines", "Guidance for external events", ["inviting", "external", "speaker"])
    assert relevant > unrelated


class FakeClient:
    def __init__(self, rows):
        self.rows = rows
        self.calls = []

    async def select(self, table, fields, params=()):
        self.calls.append((table, fields, list(params)))
        return self.rows.get(table, [])


async def test_knowledge_evidence_keeps_source_provenance():
    client = FakeClient({"knowledge_requirements": [{
        "id": "req-1", "requirement_text": "Submit the speaker approval form.", "requirement_type": "approval",
        "source_article_id": "article-1", "source_url": "https://icu.example/article", "source_content_hash": "abc",
        "source_updated_at": "2026-08-01T00:00:00Z", "evidence_text": "approval form", "review_status": "approved",
        "is_stale": False, "visibility": "committee", "mandatory": True,
    }]})
    result = await KnowledgeAdapter(client).search(RetrievalRequest(question="What is the requirement?", terms=["speaker"], max_results=4), development_context(AgentScope.COMMITTEE))
    assert result.evidence[0].citation.source_id == "req-1"
    assert result.evidence[0].citation.content_hash == "abc"
    assert any("review_status" in str(call) for call in client.calls)
    or_values = [value for _, _, params in client.calls for key, value in params if key == "or"]
    assert or_values and all(value.startswith("(") and value.endswith(")") for value in or_values)


async def test_resource_question_does_not_search_unrelated_process_tables():
    client = FakeClient({"knowledge_resources": [{
        "id": "resource-1", "name": "Committee admin request form", "resource_type": "form",
        "url": "https://example.test/form", "source_article_id": "article-1", "source_url": "https://icu.example/article",
        "source_content_hash": "abc", "source_updated_at": "2026-08-01T00:00:00Z", "review_status": "approved",
        "is_stale": False, "visibility": "committee", "description": "Admin request form",
    }]})
    result = await KnowledgeAdapter(client).search(
        RetrievalRequest(question="Where can I find the committee admin request form?", terms=["committee", "admin", "request", "form"]),
        development_context(AgentScope.COMMITTEE),
    )
    assert result.evidence
    assert [table for table, _, _ in client.calls] == ["knowledge_resources"]


async def test_member_cannot_query_slack_even_with_fake_rows():
    client = FakeClient({"slack_messages": [{"id": "secret", "message_text": "private"}]})
    result = await SlackAdapter(client).search(RetrievalRequest(question="Slack", terms=["private"]), development_context(AgentScope.MEMBER))
    assert result.evidence == []
    assert not client.calls


async def test_admin_slack_evidence_assembles_thread():
    client = FakeClient({
        "slack_messages": [{"id": "reply", "channel_id": "C1", "slack_ts": "2", "thread_ts": "1", "message_text": "speaker reply", "source_posted_at": "2026-08-01T00:01:00Z", "permalink": "https://slack/thread"}],
        "slack_channels": [{"id": "C1", "name": "events"}],
    })
    result = await SlackAdapter(client).search(RetrievalRequest(question="discussion", terms=["speaker"], max_results=4), development_context(AgentScope.ADMIN))
    assert result.evidence[0].citation.channel == "events"
    assert result.evidence[0].citation.url == "https://slack/thread"
    assert "speaker reply" in result.evidence[0].text
    thread_calls = [params for table, _, params in client.calls if table == "slack_messages"]
    assert any(("channel_id", "eq.C1") in params for params in thread_calls[1:])


async def test_document_evidence_redacts_absolute_windows_paths():
    client = FakeClient({"documents": [{
        "id": "doc-1", "title": "Operating Plan", "source_type": "onedrive_filesystem",
        "relative_path": "C:\\Users\\slee7\\OneDrive\\01_Governance\\plan.docx",
        "source_area": "01_governance", "raw_text": "See C:\\Users\\slee7\\private\\plan.docx",
        "content_hash": "hash", "filesystem_modified_at": "2026-08-01T00:00:00Z",
    }]})
    result = await DocumentAdapter(client).search(RetrievalRequest(question="operating plan", terms=["operating", "plan"]), development_context(AgentScope.ADMIN))
    assert result.evidence
    assert "C:\\Users" not in result.evidence[0].text
    assert "C:\\Users" not in (result.evidence[0].citation.path or "")


async def test_postgrest_or_filters_keep_parentheses():
    client = FakeClient({"documents": [{
        "id": "doc-1", "title": "Operating Plan", "source_type": "onedrive_filesystem",
        "relative_path": "01_Governance/plan.docx", "source_area": "01_governance",
        "raw_text": "speaker plan", "content_hash": "hash",
        "filesystem_modified_at": "2026-08-01T00:00:00Z",
    }]})
    await DocumentAdapter(client).search(RetrievalRequest(question="speaker plan", terms=["speaker", "plan"]), development_context(AgentScope.ADMIN))
    params = client.calls[0][2]
    or_value = next(value for key, value in params if key == "or")
    assert or_value == "(title.ilike.*speaker*,relative_path.ilike.*speaker*,raw_text.ilike.*speaker*,source_area.ilike.*speaker*,title.ilike.*plan*,relative_path.ilike.*plan*,raw_text.ilike.*plan*,source_area.ilike.*plan*)"
