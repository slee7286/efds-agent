from collections.abc import Sequence
from typing import Any

import httpx

from efds_agent.config import Settings

ALLOWED_TABLES = {
    "knowledge_articles", "knowledge_requirements", "knowledge_timing_rules", "knowledge_processes",
    "knowledge_process_steps", "knowledge_resources", "knowledge_contacts", "documents",
    "document_versions", "meetings", "decisions", "action_items", "slack_channels", "slack_messages",
    "slack_users", "public_knowledge_resources",
}
ALLOWED_RPCS = {"search_retrieval_units_v1"}


class DataAccessError(Exception):
    pass


class SupabaseRestClient:
    """Small allowlisted PostgREST client. There is deliberately no SQL endpoint."""

    def __init__(self, settings: Settings, bearer_token: str | None = None, client: httpx.AsyncClient | None = None) -> None:
        self.settings, self.bearer_token, self.client = settings, bearer_token, client

    def _headers(self, *, content_type: bool = False) -> dict[str, str]:
        key = self.settings.supabase_anon_key
        if not key:
            return {}
        key_value = key.get_secret_value()
        headers = {"apikey": key_value, "Authorization": f"Bearer {self.bearer_token or key_value}"}
        if content_type:
            headers["Content-Type"] = "application/json"
        return headers

    async def openapi(self) -> dict[str, Any]:
        """Read PostgREST's public schema description for contract health checks."""
        if not self.settings.is_supabase_configured:
            return {}
        url = f"{self.settings.rest_url}/"
        try:
            if self.client is not None:
                response = await self.client.get(url, headers=self._headers())
            else:
                async with httpx.AsyncClient(timeout=self.settings.request_timeout_seconds) as client:
                    response = await client.get(url, headers=self._headers())
        except httpx.RequestError as exc:
            raise DataAccessError("PostgREST schema endpoint is unreachable") from exc
        if response.status_code >= 400:
            raise DataAccessError(f"PostgREST schema endpoint failed: {response.status_code}")
        payload = response.json()
        return payload if isinstance(payload, dict) else {}

    async def select(self, table: str, fields: str, params: Sequence[tuple[str, str]] = ()) -> list[dict[str, Any]]:
        if table not in ALLOWED_TABLES: raise DataAccessError("table is not an allowlisted retrieval source")
        key = self.settings.supabase_anon_key
        if not self.settings.is_supabase_configured or not key: return []
        headers = self._headers()
        query = [("select", fields), *params]
        url = f"{self.settings.rest_url}/{table}"
        try:
            if self.client is not None: response = await self.client.get(url, headers=headers, params=query)
            else:
                async with httpx.AsyncClient(timeout=self.settings.request_timeout_seconds) as client:
                    response = await client.get(url, headers=headers, params=query)
        except httpx.RequestError as exc:
            raise DataAccessError(f"PostgREST is unreachable for {table}") from exc
        if response.status_code >= 400: raise DataAccessError(f"PostgREST retrieval failed for {table}: {response.status_code}")
        payload = response.json()
        return payload if isinstance(payload, list) else []

    async def rpc(self, function: str, payload: dict[str, Any]) -> list[dict[str, Any]]:
        """Call one fixed, RLS-aware retrieval RPC; arbitrary RPC/SQL is rejected."""
        if function not in ALLOWED_RPCS:
            raise DataAccessError("function is not an allowlisted retrieval RPC")
        key = self.settings.supabase_anon_key
        if not self.settings.is_supabase_configured or not key:
            return []
        headers = self._headers(content_type=True)
        url = f"{self.settings.rest_url}/rpc/{function}"
        try:
            if self.client is not None:
                response = await self.client.post(url, headers=headers, json=payload)
            else:
                async with httpx.AsyncClient(timeout=self.settings.request_timeout_seconds) as client:
                    response = await client.post(url, headers=headers, json=payload)
        except httpx.RequestError as exc:
            raise DataAccessError("PostgREST retrieval RPC is unreachable") from exc
        if response.status_code >= 400:
            raise DataAccessError(f"PostgREST retrieval RPC failed: {response.status_code}")
        result = response.json()
        return result if isinstance(result, list) else []

    async def probe_rpc(self, function: str, payload: dict[str, Any]) -> tuple[int, list[dict[str, Any]]]:
        """Probe one allowlisted RPC while retaining only status and rows."""
        if function not in ALLOWED_RPCS:
            raise DataAccessError("function is not an allowlisted retrieval RPC")
        if not self.settings.is_supabase_configured:
            return 0, []
        url = f"{self.settings.rest_url}/rpc/{function}"
        try:
            if self.client is not None:
                response = await self.client.post(url, headers=self._headers(content_type=True), json=payload)
            else:
                async with httpx.AsyncClient(timeout=self.settings.request_timeout_seconds) as client:
                    response = await client.post(url, headers=self._headers(content_type=True), json=payload)
        except httpx.RequestError as exc:
            raise DataAccessError("PostgREST retrieval RPC is unreachable") from exc
        try:
            body = response.json()
        except ValueError:
            body = []
        return response.status_code, body if isinstance(body, list) else []
