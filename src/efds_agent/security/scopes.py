from dataclasses import dataclass
from enum import StrEnum


class AgentScope(StrEnum):
    PUBLIC = "public"
    VIEWER = "viewer"
    MEMBER = "member"
    COMMITTEE = "committee"
    ADMIN = "admin"


class AccessRole(StrEnum):
    VIEWER = "viewer"
    MEMBER = "member"
    EFDS_MEMBER = "efds_member"
    COMMITTEE = "committee"
    ADMIN = "admin"


SCOPE_RANK = {
    AgentScope.PUBLIC: 0,
    AgentScope.VIEWER: 1,
    AgentScope.MEMBER: 2,
    AgentScope.COMMITTEE: 3,
    AgentScope.ADMIN: 4,
}


class ScopeError(ValueError):
    pass


@dataclass(frozen=True)
class ScopeDecision:
    authorized_scope: AgentScope
    requested_scope: AgentScope | None
    effective_scope: AgentScope


def scope_for_role(role: AccessRole) -> AgentScope:
    if role is AccessRole.MEMBER:
        return AgentScope.PUBLIC
    if role is AccessRole.EFDS_MEMBER:
        return AgentScope.MEMBER
    return AgentScope(role.value)


def resolve_scope(role: AccessRole | None, requested: AgentScope | None) -> ScopeDecision:
    authorized = AgentScope.PUBLIC if role is None else scope_for_role(role)
    if requested is not None and SCOPE_RANK[requested] > SCOPE_RANK[authorized]:
        raise ScopeError(f"requested scope {requested.value!r} exceeds authenticated access")
    return ScopeDecision(authorized, requested, requested or authorized)


def can_read_source(scope: AgentScope, source: str) -> bool:
    if source == "knowledge_public":
        return True
    if source == "knowledge":
        return scope in {AgentScope.MEMBER, AgentScope.COMMITTEE, AgentScope.ADMIN}
    if source in {"operational", "documents_legacy"}:
        return scope in {AgentScope.COMMITTEE, AgentScope.ADMIN}
    if source in {"documents_onedrive", "slack"}:
        return scope is AgentScope.ADMIN
    return False
