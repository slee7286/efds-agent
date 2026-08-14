import httpx
import pytest

from efds_agent.citations.formatter import validate_answer
from efds_agent.citations.models import Citation
from efds_agent.config import Settings
from efds_agent.security.authorization import SupabaseAuthorization
from efds_agent.security.scopes import AccessRole, AgentScope, ScopeError, resolve_scope


def test_requested_scope_can_lower_access():
    decision = resolve_scope(AccessRole.COMMITTEE, AgentScope.PUBLIC)
    assert decision.effective_scope is AgentScope.PUBLIC


def test_requested_scope_cannot_escalate():
    with pytest.raises(ScopeError): resolve_scope(AccessRole.MEMBER, AgentScope.ADMIN)


def test_unknown_citation_is_removed_and_known_source_is_preserved():
    citations = [Citation(id="S1", source_type="icu_article", source_id="1", title="Article")]
    answer, invalid = validate_answer("Claim [S1] and fake [S99].", citations)
    assert invalid == ["S99"]
    assert "S99" not in answer
    assert "[S1]" in answer


@pytest.mark.asyncio
async def test_verified_member_token_cannot_request_admin_scope():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/auth/v1/user"):
            return httpx.Response(200, json={"id": "user-1", "email": "member@example.com"})
        return httpx.Response(200, json=[{"auth_user_id": "user-1", "access_role": "member", "active": True}])

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    authorization = SupabaseAuthorization(Settings(supabase_url="https://example.supabase.co", supabase_anon_key="anon"), client)
    with pytest.raises(ScopeError):
        await authorization.authenticate("short-lived-token", AgentScope.ADMIN)
    await client.aclose()
