from fastapi import Request

from efds_agent.security.authorization import AuthContext, SupabaseAuthorization
from efds_agent.security.scopes import AgentScope


def bearer_token(request: Request) -> str | None:
    header = request.headers.get("authorization", "")
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer" or not token.strip(): return None
    return token.strip()


async def resolve_request_auth(request: Request, requested_scope: AgentScope | None) -> AuthContext:
    service: SupabaseAuthorization = request.app.state.authorization
    token = bearer_token(request)
    if token is None: return service.public(requested_scope)
    return await service.authenticate(token, requested_scope)
