from __future__ import annotations

from typing import Any

from efds_agent.retrieval.contract import (
    CONTRACT_VERSION,
    EXPECTED_ARGUMENTS,
    RPC_NAME,
    validate_openapi_document,
    validate_result_rows,
)
from efds_agent.retrieval.supabase import DataAccessError, SupabaseRestClient


async def retrieval_contract_health(client: SupabaseRestClient, *, probe: bool = True) -> dict[str, Any]:
    """Return safe contract diagnostics; never returns source rows or secrets."""
    base: dict[str, Any] = {
        "rpc": RPC_NAME,
        "contract_version": CONTRACT_VERSION,
        "auth_mode": "user_jwt" if client.bearer_token else "anon_key",
        "expected_arguments": list(EXPECTED_ARGUMENTS),
        "status": "unconfigured",
        "schema_path_present": False,
        "result_schema_verified": False,
    }
    if not client.settings.is_supabase_configured:
        return base
    try:
        document = await client.openapi()
        advertised = validate_openapi_document(document)
    except DataAccessError as exc:
        advertised = False
        base["schema_error"] = str(exc)
    base["schema_path_present"] = advertised
    if not advertised and not probe:
        return {**base, "status": "unavailable", "error_category": "rpc_not_in_postgrest_schema"}
    if advertised and not probe:
        return {**base, "status": "configured"}
    try:
        status_code, rows = await client.probe_rpc(
            RPC_NAME,
            {
                "search_query": "EFDS contract health check",
                "requested_source_types": None,
                "requested_area": None,
                "requested_topic": None,
                "requested_channel": None,
                "requested_author": None,
                "requested_from": None,
                "requested_to": None,
                "include_history": False,
                "result_limit": 1,
                "result_offset": 0,
            },
        )
    except DataAccessError as exc:
        return {**base, "status": "unavailable", "error_category": str(exc)}
    if status_code >= 400:
        category = (
            "rpc_not_found"
            if status_code == 404
            else "authentication_rejected"
            if status_code in {401, 403}
            else "rpc_probe_failed"
        )
        return {**base, "status": "unavailable", "http_status": status_code, "error_category": category}
    try:
        validate_result_rows(rows)
    except ValueError as exc:
        return {
            **base,
            "status": "unavailable",
            "http_status": status_code,
            "error_category": "contract_schema_invalid",
            "contract_error": str(exc),
        }
    return {
        **base,
        "status": "ready",
        "http_status": status_code,
        "probe_result_count": len(rows),
        "result_schema_verified": bool(rows),
    }
