from dataclasses import dataclass
from typing import Any

import httpx
from pydantic import BaseModel, ConfigDict

from efds_agent.config import Settings
from efds_agent.security.scopes import AccessRole, AgentScope, ScopeDecision, resolve_scope


class AuthenticatedUser(BaseModel):
    model_config = ConfigDict(frozen=True)
    id: str
    email: str | None = None


class AuthError(Exception):
    pass


@dataclass(frozen=True)
class AuthContext:
    user: AuthenticatedUser | None
    access_role: AccessRole | None
    scope: ScopeDecision
    bearer_token: str | None = None
    development_simulation: bool = False


class SupabaseAuthorization:
    """Validate identity with Supabase Auth, then resolve role via RLS-protected profiles."""

    def __init__(self, settings: Settings, client: httpx.AsyncClient | None = None) -> None:
        self.settings = settings
        self.client = client

    @property
    def _anon_key(self) -> str:
        key = self.settings.supabase_anon_key
        if not key: raise AuthError("Supabase is not configured")
        return key.get_secret_value()

    async def _request(self, method: str, url: str, token: str, **kwargs: Any) -> httpx.Response:
        headers = {"apikey": self._anon_key, "Authorization": f"Bearer {token}"}
        if self.client is not None: return await self.client.request(method, url, headers=headers, **kwargs)
        async with httpx.AsyncClient(timeout=self.settings.request_timeout_seconds) as client:
            return await client.request(method, url, headers=headers, **kwargs)

    async def authenticate(self, token: str, requested_scope: AgentScope | None) -> AuthContext:
        if not self.settings.is_supabase_configured: raise AuthError("Supabase is not configured")
        if not token or len(token) > 10000: raise AuthError("invalid bearer token")
        try:
            response = await self._request("GET", self.settings.auth_url, token)
        except httpx.RequestError as exc:
            raise AuthError("Supabase Auth is unreachable") from exc
        if response.status_code != 200:
            # Do not expose Supabase's response body: it may contain provider
            # diagnostics or details that are not useful to a client.
            raise AuthError(
                f"Supabase identity verification failed (HTTP {response.status_code}; "
                "the bearer token may be expired or belong to another project)"
            )
        try:
            payload = response.json()
            user = AuthenticatedUser(id=str(payload["id"]), email=payload.get("email"))
        except (KeyError, TypeError, ValueError) as exc:
            raise AuthError("Supabase returned an invalid identity") from exc
        try:
            profile_response = await self._request(
                "GET", self.settings.rest_url + "/profiles", token,
                params={"select": "access_role,active,auth_user_id", "auth_user_id": f"eq.{user.id}", "limit": "1"},
            )
        except httpx.RequestError as exc:
            raise AuthError("Supabase profile lookup is unreachable") from exc
        if profile_response.status_code != 200: raise AuthError("profile authorization lookup failed")
        rows = profile_response.json()
        if not isinstance(rows, list) or not rows: raise AuthError("active EFDS profile required")
        profile = rows[0]
        if profile.get("active") is not True or profile.get("auth_user_id") != user.id: raise AuthError("active EFDS profile required")
        try: role = AccessRole(str(profile["access_role"]))
        except (KeyError, ValueError) as exc: raise AuthError("profile has an invalid access role") from exc
        return AuthContext(user, role, resolve_scope(role, requested_scope), token)

    def public(self, requested_scope: AgentScope | None) -> AuthContext:
        if requested_scope not in (None, AgentScope.PUBLIC): raise AuthError("authentication is required for a private scope")
        return AuthContext(None, None, resolve_scope(None, AgentScope.PUBLIC))


def development_context(scope: AgentScope) -> AuthContext:
    """CLI/test-only scope simulation; never used by HTTP dependencies."""
    role = None if scope is AgentScope.PUBLIC else AccessRole(scope.value)
    return AuthContext(None, role, resolve_scope(role, scope), development_simulation=True)
