import hmac
import json
import uuid
from collections.abc import AsyncIterator

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

from efds_agent import __version__
from efds_agent.agent.orchestrator import AgentOrchestrator
from efds_agent.api.auth import resolve_request_auth
from efds_agent.api.schemas import (
    HealthResponse,
    QueryRequest,
    QueryResponse,
    RetrievalEvidenceResponse,
    RetrievalResponse,
)
from efds_agent.config import Settings
from efds_agent.providers.base import ProviderError
from efds_agent.retrieval.gateway import RetrievalDependencyError
from efds_agent.retrieval.health import retrieval_contract_health
from efds_agent.retrieval.modes import SourceMode, mode_certification
from efds_agent.retrieval.supabase import SupabaseRestClient
from efds_agent.security.authorization import AuthError
from efds_agent.security.scopes import AgentScope, ScopeError

router = APIRouter()


def _check_service_secret(request: Request) -> None:
    configured = request.app.state.settings.agent_shared_secret
    if configured and not hmac.compare_digest(
        request.headers.get("x-efds-agent-secret", ""), configured.get_secret_value()
    ):
        raise HTTPException(status_code=403, detail="agent service authentication required")


@router.get("/health", response_model=HealthResponse)
async def health(request: Request) -> HealthResponse:
    settings: Settings = request.app.state.settings
    contract = await retrieval_contract_health(SupabaseRestClient(settings), probe=False)
    return HealthResponse(
        status="ok",
        service=settings.app_name,
        version=__version__,
        retrieval="configured" if settings.is_supabase_configured else "not_configured",
        model="configured" if settings.ai_api_key else "not_configured",
        modes={mode.value: mode_certification(mode) for mode in SourceMode},
        retrieval_contract=contract,
    )


@router.get("/v1/health/retrieval")
async def retrieval_health(request: Request) -> dict[str, object]:
    """Check the fixed RPC without exposing retrieval data or credentials."""
    settings: Settings = request.app.state.settings
    return await retrieval_contract_health(SupabaseRestClient(settings), probe=True)


async def _auth_or_http(request: Request, scope: AgentScope | None):
    try:
        return await resolve_request_auth(request, scope)
    except (AuthError, ScopeError) as exc:
        status = 403 if isinstance(exc, ScopeError) else 401
        raise HTTPException(status_code=status, detail=str(exc)) from exc


def _check_mode_access(auth, source_mode: SourceMode) -> None:
    if (
        source_mode in {SourceMode.FULL_INSTITUTIONAL, SourceMode.ADMIN_OUTLOOK_TICKETS}
        and auth.scope.effective_scope is not AgentScope.ADMIN
    ):
        raise HTTPException(status_code=403, detail="this source mode requires admin scope")
    if source_mode is SourceMode.COMMITTEE_TICKETS and auth.scope.effective_scope not in {
        AgentScope.COMMITTEE,
        AgentScope.ADMIN,
    }:
        raise HTTPException(status_code=403, detail="committee ticket mode requires committee scope")


@router.post("/v1/query", response_model=QueryResponse)
async def query(payload: QueryRequest, request: Request) -> QueryResponse:
    _check_service_secret(request)
    auth = await _auth_or_http(request, payload.scope)
    _check_mode_access(auth, payload.source_mode)
    orchestrator: AgentOrchestrator = request.app.state.orchestrator
    try:
        result = await orchestrator.answer(
            payload.query.strip(), auth, source_mode=payload.source_mode, conversation=payload.conversation
        )
    except RetrievalDependencyError as exc:
        raise HTTPException(status_code=503, detail="Canonical retrieval is temporarily unavailable") from exc
    except ProviderError as exc:
        raise HTTPException(status_code=502, detail="The answer provider is temporarily unavailable") from exc
    return QueryResponse.model_validate(result.model_dump())


@router.post("/v1/retrieval", response_model=RetrievalResponse)
async def retrieval_only(payload: QueryRequest, request: Request) -> RetrievalResponse:
    """Admin/developer dogfood endpoint; uses the exact production gateway, no model."""
    _check_service_secret(request)
    auth = await _auth_or_http(request, payload.scope)
    _check_mode_access(auth, payload.source_mode)
    orchestrator: AgentOrchestrator = request.app.state.orchestrator
    try:
        _plan, context, _results = await orchestrator.retrieve(
            payload.query.strip(), auth, source_mode=payload.source_mode, conversation=payload.conversation
        )
    except RetrievalDependencyError as exc:
        raise HTTPException(status_code=503, detail="Canonical retrieval is temporarily unavailable") from exc
    evidence = []
    for item in context.items:
        citation = item.citation
        evidence.append(
            RetrievalEvidenceResponse(
                id=citation.id,
                retrieval_unit_id=citation.retrieval_unit_id,
                source_type=citation.source_type,
                source_record_id=citation.source_record_id,
                title=citation.title,
                authority=citation.authority,
                score=item.relevance,
                visibility=str(citation.metadata.get("visibility")) if citation.metadata.get("visibility") else None,
                is_current=bool(item.metadata.get("is_current", True)),
                is_stale=bool(item.metadata.get("is_stale", False)),
                provenance_preview=(citation.excerpt or "")[:240],
            )
        )
    return RetrievalResponse(
        request_id=uuid.uuid4().hex,
        query=context.query,
        scope=auth.scope.effective_scope,
        source_mode=payload.source_mode.value,
        retrieval_quality=str(context.retrieval_metadata.get("retrieval_quality", "low")),
        result_count=len(evidence),
        retrieval_metadata=context.retrieval_metadata,
        evidence=evidence,
    )


@router.post("/v1/query/stream")
async def query_stream(payload: QueryRequest, request: Request) -> StreamingResponse:
    _check_service_secret(request)
    auth = await _auth_or_http(request, payload.scope)
    _check_mode_access(auth, payload.source_mode)
    orchestrator: AgentOrchestrator = request.app.state.orchestrator

    async def events() -> AsyncIterator[str]:
        async for event in orchestrator.stream(
            payload.query.strip(), auth, source_mode=payload.source_mode, conversation=payload.conversation
        ):
            yield f"event: {event['event']}\ndata: {json.dumps(event, default=str)}\n\n"

    return StreamingResponse(
        events(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
    )


@router.get("/v1/debug/sources")
async def debug_sources(request: Request):
    _check_service_secret(request)
    settings: Settings = request.app.state.settings
    if settings.app_env not in {"development", "test"}:
        auth = await _auth_or_http(request, AgentScope.ADMIN)
        if auth.access_role is None or auth.access_role.value != "admin":
            raise HTTPException(status_code=403, detail="admin required")
    return {
        "sources": ["canonical_retrieval_rpc"],
        "retrieval_rpc": "search_retrieval_units_v1",
        "rls_first": True,
        "service_role_used": False,
        "arbitrary_sql": False,
        "arbitrary_filesystem": False,
    }
