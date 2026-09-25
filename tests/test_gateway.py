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


class FakeRpcRouter:
    """Return different rows per RPC name, so contract fallback can be tested."""

    def __init__(self, responses, default=None):
        self.responses = responses
        self.default = default if default is not None else []
        self.calls = []

    async def rpc(self, function, payload):
        self.calls.append((function, payload))
        return self.responses.get(function, self.default)

    def functions_called(self):
        return [name for name, _ in self.calls]


MULTI_ROW_METADATA = {"visibility": "committee"}


def multi_row(**values):
    """A row shaped like the migration 0023 multi-query contract."""
    defaults = {
        "id": "u1",
        "retrieval_unit_id": "u1",
        "source_version_id": None,
        "source_parent_id": None,
        "source_area": None,
        "topic": None,
        "channel": None,
        "author": None,
        "occurred_at": None,
        "source_updated_at": None,
        "review_status": "approved",
        "is_current": True,
        "is_stale": False,
        "source_url": None,
        "permalink": None,
        "relative_path": None,
        "content_hash": "h",
        "metadata": dict(MULTI_ROW_METADATA),
        "authority": "approved_knowledge",
        "content": "content",
        "lexical_score": 0.01,
        "semantic_score": 0.0,
        "fused_score": 0.01,
        "matched_queries": 1,
        "best_lexical_rank": 1,
        "semantic_rank": None,
    }
    defaults.update(values)
    return defaults


def committee_auth(token: str | None = "test-bearer-token") -> AuthContext:
    """A committee context. `token=None` models a request PostgREST sees as anonymous."""
    return AuthContext(
        None,
        AccessRole.COMMITTEE,
        resolve_scope(AccessRole.COMMITTEE, AgentScope.COMMITTEE),
        bearer_token=token,
        development_simulation=True,
    )


def row(**values):
    defaults = {
        "source_version_id": None,
        "source_parent_id": None,
        "source_area": None,
        "topic": None,
        "channel": None,
        "author": None,
        "occurred_at": None,
        "source_updated_at": None,
        "review_status": "approved",
        "is_current": True,
        "is_stale": False,
        "source_url": None,
        "permalink": None,
        "relative_path": None,
        "content_hash": None,
        "metadata": {},
        "authority": "approved_knowledge",
    }
    defaults.update(values)
    return defaults


@pytest.mark.asyncio
async def test_gateway_falls_back_to_v1_and_preserves_backend_order():
    # The fake answers every RPC with v1-shaped rows, so the multi-query
    # contract fails validation and the gateway must fall back cleanly.
    client = FakeRpc(
        [
            row(
                retrieval_unit_id="u2",
                source_type="knowledge_requirement",
                source_record_id="r2",
                title="Second",
                snippet="second",
                score=0.2,
                visibility="committee",
            ),
            row(
                retrieval_unit_id="u1",
                source_type="knowledge_requirement",
                source_record_id="r1",
                title="First",
                snippet="first",
                score=0.9,
                visibility="committee",
            ),
        ]
    )
    gateway = KnowledgeRetrievalGateway(Settings(_env_file=None), client)
    package = await gateway.retrieve(
        RetrievalRequest(query="question", scope="committee", limit=10), development_context(AgentScope.COMMITTEE)
    )
    assert "search_retrieval_units_v1" in [name for name, _ in client.calls]
    assert package.retrieval_metadata["retrieval_strategy"] == "canonical_backend_order"
    assert [item.citation.retrieval_unit_id for item in package.items] == ["u2", "u1"]
    assert [item.id for item in package.citations] == ["S1", "S2"]


@pytest.mark.asyncio
async def test_gateway_prefers_multi_query_contract_for_authenticated_callers():
    client = FakeRpcRouter(
        {
            "retrieval_embedding_profile": [
                {
                    "provider": "openai",
                    "model": "text-embedding-3-small",
                    "model_version": "1",
                    "dimension": 1536,
                    "embedded_units": 4,
                    "indexed_units": 4,
                }
            ],
            "search_retrieval_units_multi": [
                multi_row(
                    id="u9",
                    retrieval_unit_id="u9",
                    source_type="knowledge_requirement",
                    source_record_id="r9",
                    title="Hybrid",
                    content="hybrid body",
                    fused_score=0.5,
                    visibility="committee",
                ),
            ],
        }
    )
    gateway = KnowledgeRetrievalGateway(Settings(_env_file=None, ai_api_key="test-key"), client)
    package = await gateway.retrieve(RetrievalRequest(query="question", scope="committee", limit=10), committee_auth())
    assert package.retrieval_metadata["retrieval_strategy"] == "multi_query_hybrid_rrf"
    assert "search_retrieval_units_v1" not in client.functions_called()
    # Content, not a ts_headline snippet, is what the model grounds on.
    assert package.items[0].text == "hybrid body"


@pytest.mark.asyncio
async def test_gateway_uses_public_entry_point_when_no_bearer_token_is_present():
    # A role-scoped context with no bearer token is treated by PostgREST as
    # anonymous, so it must never reach the role-checking entry point.
    client = FakeRpcRouter(
        {
            "retrieval_embedding_profile": [
                {
                    "provider": "openai",
                    "model": "text-embedding-3-small",
                    "model_version": "1",
                    "dimension": 1536,
                    "embedded_units": 1,
                    "indexed_units": 1,
                }
            ],
            "search_retrieval_units_public": [
                multi_row(
                    id="p1",
                    retrieval_unit_id="p1",
                    source_type="knowledge_resource",
                    source_record_id="r1",
                    title="Public",
                    content="public body",
                    visibility="public",
                    fused_score=0.4,
                ),
            ],
        }
    )
    gateway = KnowledgeRetrievalGateway(Settings(_env_file=None, ai_api_key="test-key"), client)
    package = await gateway.retrieve(RetrievalRequest(query="question", scope="committee"), committee_auth(token=None))
    assert "search_retrieval_units_multi" not in client.functions_called()
    assert package.items[0].citation.retrieval_unit_id == "p1"


@pytest.mark.asyncio
async def test_preterm_narrows_documents_to_governance_without_reordering():
    client = FakeRpc(
        [
            row(
                retrieval_unit_id="bad",
                source_type="document",
                source_record_id="d1",
                source_area="02_events",
                title="Bad",
                snippet="bad",
                score=1,
                visibility="internal",
            ),
            row(
                retrieval_unit_id="good",
                source_type="document",
                source_record_id="d2",
                source_area="01_governance",
                title="Good",
                snippet="good",
                score=0.5,
                visibility="internal",
            ),
        ]
    )
    gateway = KnowledgeRetrievalGateway(Settings(_env_file=None), client)
    package = await gateway.retrieve(
        RetrievalRequest(query="governance", scope="admin", source_mode=SourceMode.PRETERM_KNOWLEDGE),
        development_context(AgentScope.ADMIN),
    )
    assert [item.citation.retrieval_unit_id for item in package.items] == ["good"]


@pytest.mark.asyncio
async def test_requested_lower_scope_filters_higher_visibility_rows():
    client = FakeRpc(
        [
            row(
                retrieval_unit_id="committee",
                source_type="knowledge_requirement",
                source_record_id="r1",
                visibility="committee",
                title="Committee",
                snippet="committee",
                score=1,
            ),
            row(
                retrieval_unit_id="member",
                source_type="knowledge_requirement",
                source_record_id="r2",
                visibility="member",
                title="Member",
                snippet="member",
                score=0.8,
            ),
            row(
                retrieval_unit_id="public",
                source_type="knowledge_requirement",
                source_record_id="r3",
                visibility="public",
                title="Public",
                snippet="public",
                score=0.6,
            ),
        ]
    )
    gateway = KnowledgeRetrievalGateway(Settings(_env_file=None), client)
    auth = AuthContext(
        None, AccessRole.MEMBER, resolve_scope(AccessRole.MEMBER, AgentScope.PUBLIC), development_simulation=True
    )
    package = await gateway.retrieve(RetrievalRequest(query="question", scope="public"), auth)
    assert [item.citation.retrieval_unit_id for item in package.items] == ["public"]


@pytest.mark.asyncio
async def test_missing_contract_authority_fails_closed():
    client = FakeRpc(
        [
            row(
                retrieval_unit_id="broken",
                source_type="knowledge_requirement",
                source_record_id="r",
                title="Broken",
                snippet="broken",
                score=1,
                visibility="public",
            )
        ]
    )
    client.rows[0].pop("authority")
    gateway = KnowledgeRetrievalGateway(Settings(_env_file=None), client)
    with pytest.raises(Exception, match="canonical retrieval contract"):
        await gateway.retrieve(
            RetrievalRequest(query="question", scope="public"), development_context(AgentScope.PUBLIC)
        )


@pytest.mark.asyncio
async def test_supabase_rpc_propagates_user_jwt_and_never_service_role():
    captured = {}

    def handler(request: httpx.Request):
        captured["url"] = str(request.url)
        captured["headers"] = dict(request.headers)
        captured["json"] = request.content
        return httpx.Response(200, json=[])

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    rest = SupabaseRestClient(
        Settings(_env_file=None, supabase_url="https://example.supabase.co", supabase_anon_key="anon"),
        "user-jwt",
        client,
    )
    await rest.rpc("search_retrieval_units_v1", {"search_query": "x"})
    assert captured["url"].endswith("/rest/v1/rpc/search_retrieval_units_v1")
    assert captured["headers"]["authorization"] == "Bearer user-jwt"
    assert "service_role" not in str(captured).lower()
    await client.aclose()


@pytest.mark.asyncio
async def test_publishable_key_uses_apikey_only_until_a_user_jwt_is_available():
    requests = []

    def handler(request: httpx.Request):
        requests.append(request)
        return httpx.Response(200, json=[])

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        settings = Settings(
            _env_file=None, supabase_url="https://example.supabase.co", supabase_publishable_key="sb_publishable_test"
        )
        await SupabaseRestClient(settings, client=client).rpc("search_retrieval_units_v1", {"search_query": "public"})
        await SupabaseRestClient(settings, "user-jwt", client).rpc(
            "search_retrieval_units_v1", {"search_query": "member"}
        )

    assert requests[0].headers["apikey"] == "sb_publishable_test"
    assert "authorization" not in requests[0].headers
    assert requests[1].headers["authorization"] == "Bearer user-jwt"


@pytest.mark.asyncio
async def test_committee_ticket_mode_uses_bounded_public_channel_rpc_and_rejects_untrusted_rows():
    message_id = "22222222-2222-4222-8222-222222222222"
    client = FakeRpc(
        [
            row(
                retrieval_unit_id="safe",
                source_type="slack_message",
                source_record_id=message_id,
                title="#events",
                snippet="Confirm the venue",
                score=0.8,
                visibility="committee",
                review_status="source_generated",
                authority="committee_slack",
            ),
            row(
                retrieval_unit_id="private",
                source_type="slack_message",
                source_record_id="private",
                title="#private",
                snippet="Private detail",
                score=0.9,
                visibility="internal",
                review_status="source_generated",
                authority="committee_slack",
            ),
            row(
                retrieval_unit_id="wrong",
                source_type="meeting_notes",
                source_record_id="meeting",
                title="Meeting",
                snippet="Private notes",
                score=1,
                visibility="committee",
                review_status="source_generated",
                authority="committee_slack",
            ),
        ]
    )
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


@pytest.mark.asyncio
async def test_outlook_ticket_mode_is_admin_only_and_uses_the_private_rpc():
    client = FakeRpc(
        [
            row(
                retrieval_unit_id="mail",
                source_type="outlook_message",
                source_record_id="mail-id",
                title="Event update",
                snippet="Confirm the date",
                score=0.7,
                visibility="internal",
                review_status="source_generated",
                authority="outlook_mail",
            ),
            row(
                retrieval_unit_id="wrong",
                source_type="outlook_message",
                source_record_id="wrong-id",
                title="Wrong",
                snippet="Do not show",
                score=0.9,
                visibility="internal",
                review_status="source_generated",
                authority="other",
            ),
        ]
    )
    gateway = KnowledgeRetrievalGateway(Settings(_env_file=None), client)
    package = await gateway.retrieve(
        RetrievalRequest(query="suggest action", scope="admin", source_mode=SourceMode.ADMIN_OUTLOOK_TICKETS),
        development_context(AgentScope.ADMIN),
    )
    assert client.calls == [("admin_outlook_ticket_evidence_v1", {"result_limit": 12})]
    assert [item.citation.retrieval_unit_id for item in package.items] == ["mail"]
    with pytest.raises(RetrievalDependencyError, match="admin scope"):
        await gateway.retrieve(
            RetrievalRequest(query="suggest action", scope="committee", source_mode=SourceMode.ADMIN_OUTLOOK_TICKETS),
            development_context(AgentScope.COMMITTEE),
        )
    assert len(client.calls) == 1
