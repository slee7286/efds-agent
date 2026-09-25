"""Validation for the versioned retrieval RPC response contract.

The agent intentionally validates the boundary instead of filling in missing
provenance or authority fields.  A missing field can indicate an unapplied
migration or a stale PostgREST schema cache and must fail visibly.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

CONTRACT_VERSION = "1"
RPC_NAME = "search_retrieval_units_v1"

# These fields are returned by migration 0014 and are needed to preserve
# ordering, authorization, provenance, temporal state, and citation mapping.
REQUIRED_RESULT_FIELDS = frozenset(
    {
        "retrieval_unit_id",
        "source_type",
        "source_record_id",
        "source_version_id",
        "title",
        "snippet",
        "score",
        "source_area",
        "topic",
        "channel",
        "author",
        "occurred_at",
        "source_updated_at",
        "review_status",
        "visibility",
        "is_current",
        "is_stale",
        "source_url",
        "permalink",
        "relative_path",
        "content_hash",
        "metadata",
        "authority",
    }
)

EXPECTED_ARGUMENTS = (
    "search_query",
    "requested_source_types",
    "requested_area",
    "requested_topic",
    "requested_channel",
    "requested_author",
    "requested_from",
    "requested_to",
    "include_history",
    "result_limit",
    "result_offset",
)


class RetrievalContractError(ValueError):
    """The configured retrieval RPC did not return the versioned contract."""


def validate_result_rows(rows: list[dict[str, Any]]) -> None:
    """Validate all returned rows without changing their order or values."""
    for index, row in enumerate(rows, start=1):
        missing = sorted(REQUIRED_RESULT_FIELDS.difference(row))
        if missing:
            raise RetrievalContractError(
                f"{RPC_NAME} contract v{CONTRACT_VERSION} row {index} is missing: {', '.join(missing)}"
            )
        if not isinstance(row["metadata"], dict):
            raise RetrievalContractError(
                f"{RPC_NAME} contract v{CONTRACT_VERSION} row {index} metadata is not an object"
            )


# --------------------------------------------------------------------------
# Multi-query hybrid contract (migration 0023)
# --------------------------------------------------------------------------

MULTI_RPC_NAMES = ("search_retrieval_units_multi", "search_retrieval_units_public")
CONTRACT_VERSION_V2 = "2"

# The hybrid contract returns the chunk `content` rather than a ts_headline
# `snippet`, plus the fusion components that let the caller see why a row
# ranked where it did.
REQUIRED_MULTI_FIELDS = frozenset(
    {
        "id",
        "source_type",
        "source_record_id",
        "source_version_id",
        "title",
        "content",
        "source_area",
        "topic",
        "channel",
        "author",
        "occurred_at",
        "source_updated_at",
        "review_status",
        "visibility",
        "is_current",
        "is_stale",
        "authority",
        "source_url",
        "permalink",
        "relative_path",
        "content_hash",
        "metadata",
        "lexical_score",
        "semantic_score",
        "fused_score",
        "matched_queries",
    }
)

MULTI_EXPECTED_ARGUMENTS = (
    "search_queries",
    "query_embedding",
    "requested_source_types",
    "requested_area",
    "requested_topic",
    "requested_channel",
    "requested_author",
    "requested_from",
    "requested_to",
    "requested_provider",
    "requested_model",
    "requested_model_version",
    "semantic_floor",
    "result_limit",
    "result_offset",
)


def validate_multi_query_rows(rows: list[dict[str, Any]]) -> None:
    """Validate the multi-query hybrid contract.

    An empty result is valid and is not treated as a contract failure.
    """
    for index, row in enumerate(rows, start=1):
        missing = sorted(REQUIRED_MULTI_FIELDS.difference(row))
        if missing:
            raise RetrievalContractError(
                f"multi-query retrieval contract v{CONTRACT_VERSION_V2} row {index} is missing: {', '.join(missing)}"
            )
        if not isinstance(row["metadata"], dict):
            raise RetrievalContractError(
                f"multi-query retrieval contract v{CONTRACT_VERSION_V2} row {index} metadata is not an object"
            )


def validate_openapi_document(document: Mapping[str, Any]) -> bool:
    """Return whether PostgREST advertises the fixed RPC path."""
    paths = document.get("paths")
    return isinstance(paths, Mapping) and f"/rpc/{RPC_NAME}" in paths
