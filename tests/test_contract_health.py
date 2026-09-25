import httpx
import pytest

from efds_agent.config import Settings
from efds_agent.retrieval.health import retrieval_contract_health
from efds_agent.retrieval.supabase import SupabaseRestClient


@pytest.mark.asyncio
async def test_contract_health_reports_missing_rpc_without_exposing_body():
    def handler(request: httpx.Request):
        if request.method == "GET":
            return httpx.Response(401, json={"secret": "must-not-leak"})
        return httpx.Response(404, json={"code": "PGRST202", "details": "private details"})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as http:
        client = SupabaseRestClient(
            Settings(_env_file=None, supabase_url="https://example.supabase.co", supabase_anon_key="anon"), client=http
        )
        result = await retrieval_contract_health(client)
    assert result["status"] == "unavailable"
    assert result["http_status"] == 404
    assert result["error_category"] == "rpc_not_found"
    assert "private details" not in str(result)


@pytest.mark.asyncio
async def test_contract_health_validates_returned_schema():
    def handler(request: httpx.Request):
        if request.method == "GET":
            return httpx.Response(200, json={"paths": {"/rpc/search_retrieval_units_v1": {}}})
        return httpx.Response(200, json=[])

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = SupabaseRestClient(
            Settings(_env_file=None, supabase_url="https://example.supabase.co", supabase_anon_key="anon"), client=http
        )
        result = await retrieval_contract_health(client)
    assert result["status"] == "ready"
    assert result["contract_version"] == "1"
