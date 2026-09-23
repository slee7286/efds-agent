import httpx
import pytest

from efds_agent.config import Settings
from efds_agent.retrieval.gateway import KnowledgeRetrievalGateway, RetrievalDependencyError, RetrievalRequest
from efds_agent.retrieval.modes import SourceMode
from efds_agent.retrieval.supabase import SupabaseRestClient
from efds_agent.security.authorization import AuthContext, development_context
from efds_agent.security.scopes import AccessRole, AgentScope, resolve_scope


class FakeRpc:
    def __init__(self, rows):
        self.rows = rows
        self.calls = []

    async def rpc(self, function, payload):
        self.calls.append((function, payload))
        return self.rows


def row(**values):
    defaults = {
        "source_version_id": None, "source_parent_id": None, "source_area": None,
        "topic": None, "channel": None, "author": None, "occurred_at": None,
        "source_updated_at": None, "review_status": "approved", "is_current": True,
        "is_stale": False, "source_url": None, "permalink": None, "relative_path": None,
        "content_hash": None, "metadata": {}, "authority": "approved_knowledge",
    }
    defaults.update(values)
    return defaults


@pytest.mark.asyncio
async def test_gateway_calls_only_fixed_rpc_and_preserves_backend_order():
    client = FakeRpc([
        row(retrieval_unit_id="u2", source_type="knowledge_requirement", source_record_id="r2", title="Second", snippet="second", score=0.2, visibility="committee"),
        row(retrieval_unit_id="u1", source_type="knowledge_requirement", source_record_id="r1", title="First", snippet="first", score=0.9, visibility="committee"),
    ])
    gateway = KnowledgeRetrievalGateway(Settings(_env_file=None), client)
    package = await gateway.retrieve(RetrievalRequest(query="question", scope="committee", limit=10), development_context(AgentScope.COMMITTEE))
    assert client.calls[0][0] == "search_retrieval_units_v1"
    assert package.retrieval_metadata["retrieval_strategy"] == "canonical_backend_order"
    assert [item.citation.retrieval_unit_id for item in package.items] == ["u2", "u1"]
    assert [item.id for item in package.citations] == ["S1", "S2"]


@pytest.mark.asyncio
async def test_preterm_narrows_documents_to_governance_without_reordering():
    client = FakeRpc([
        row(retrieval_unit_id="bad", source_type="document", source_record_id="d1", source_area="02_events", title="Bad", snippet="bad", score=1, visibility="internal"),
        row(retrieval_unit_id="good", source_type="document", source_record_id="d2", source_area="01_governance", title="Good", snippet="good", score=0.5, visibility="internal"),
    ])
    gateway = KnowledgeRetrievalGateway(Settings(_env_file=None), client)
    package = await gateway.retrieve(RetrievalRequest(query="governance", scope="admin", source_mode=SourceMode.PRETERM_KNOWLEDGE), development_context(AgentScope.ADMIN))
    assert [item.citation.retrieval_unit_id for item in package.items] == ["good"]


@pytest.mark.asyncio
async def test_requested_lower_scope_filters_higher_visibility_rows():
    client = FakeRpc([
        row(retrieval_unit_id="committee", source_type="knowledge_requirement", source_record_id="r1", visibility="committee", title="Committee", snippet="committee", score=1),
        row(retrieval_unit_id="member", source_type="knowledge_requirement", source_record_id="r2", visibility="member", title="Member", snippet="member", score=0.8),
        row(retrieval_unit_id="public", source_type="knowledge_requirement", source_record_id="r3", visibility="public", title="Public", snippet="public", score=0.6),
    ])
    gateway = KnowledgeRetrievalGateway(Settings(_env_file=None), client)
    auth = AuthContext(None, AccessRole.MEMBER, resolve_scope(AccessRole.MEMBER, AgentScope.PUBLIC), development_simulation=True)
    package = await gateway.retrieve(RetrievalRequest(query="question", scope="public"), auth)
    assert [item.citation.retrieval_unit_id for item in package.items] == ["public"]


@pytest.mark.asyncio
async def test_missing_contract_authority_fails_closed():
    client = FakeRpc([row(retrieval_unit_id="broken", source_type="knowledge_requirement", source_record_id="r",
                          title="Broken", snippet="broken", score=1, visibility="public")])
    client.rows[0].pop("authority")
    gateway = KnowledgeRetrievalGateway(Settings(_env_file=None), client)
    with pytest.raises(Exception, match="canonical retrieval contract"):
        await gateway.retrieve(RetrievalRequest(query="question", scope="public"), development_context(AgentScope.PUBLIC))


@pytest.mark.asyncio
async def test_supabase_rpc_propagates_user_jwt_and_never_service_role():
    captured = {}

    def handler(request: httpx.Request):
        captured["url"] = str(request.url)
        captured["headers"] = dict(request.headers)
        captured["json"] = request.content
        return httpx.Response(200, json=[])

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    rest = SupabaseRestClient(Settings(_env_file=None, supabase_url="https://example.supabase.co", supabase_anon_key="anon"), "user-jwt", client)
    await rest.rpc("search_retrieval_units_v1", {"search_query": "x"})
    assert captured["url"].endswith("/rest/v1/rpc/search_retrieval_units_v1")
    assert captured["headers"]["authorization"] == "Bearer user-jwt"
    assert "service_role" not in str(captured).lower()
    await client.aclose()


@pytest.mark.asyncio
async def test_committee_ticket_mode_uses_bounded_public_channel_rpc_and_rejects_untrusted_rows():
    message_id = "22222222-2222-4222-8222-222222222222"
    client = FakeRpc([
        row(retrieval_unit_id="safe", source_type="slack_message", source_record_id=message_id,
            title="#events", snippet="Confirm the venue", score=0.8, visibility="committee",
            review_status="source_generated", authority="committee_slack"),
        row(retrieval_unit_id="private", source_type="slack_message", source_record_id="private",
            title="#private", snippet="Private detail", score=0.9, visibility="internal",
            review_status="source_generated", authority="committee_slack"),
        row(retrieval_unit_id="wrong", source_type="meeting_notes", source_record_id="meeting",
            title="Meeting", snippet="Private notes", score=1, visibility="committee",
            review_status="source_generated", authority="committee_slack"),
    ])
    gateway = KnowledgeRetrievalGateway(Settings(_env_file=None), client)
    package = await gateway.retrieve(
        RetrievalRequest(query="suggest action", scope="committee", source_mode=SourceMode.COMMITTEE_TICKETS),
        development_context(AgentScope.COMMITTEE),
    )
    assert client.calls == [("committee_ticket_slack_evidence_v1", {"result_limit": 12})]
    assert [item.citation.retrieval_unit_id for item in package.items] == ["safe"]
    assert package.citations[0].route == f"/dashboard/slack/messages/{message_id}"
    assert package.citations[0].metadata["visibility"] == "committee"


@pytest.mark.asyncio
async def test_committee_ticket_mode_denies_member_before_rpc_call():
    client = FakeRpc([])
    gateway = KnowledgeRetrievalGateway(Settings(_env_file=None), client)
    with pytest.raises(RetrievalDependencyError, match="committee scope"):
        await gateway.retrieve(
            RetrievalRequest(query="suggest action", scope="member", source_mode=SourceMode.COMMITTEE_TICKETS),
            development_context(AgentScope.MEMBER),
        )
    assert client.calls == []
