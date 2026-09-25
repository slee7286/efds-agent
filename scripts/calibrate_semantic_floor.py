#!/usr/bin/env python
"""Calibrate SEMANTIC_FLOOR against the live multi-query retrieval function.

The fused retrieval path mixes a lexical ranking with a semantic one. Rank
fusion is rank-based because `ts_rank_cd` and cosine distance are not
commensurable, but that also makes it scale-blind: a weak semantic match earns
almost the same rank credit as a strong lexical one. Without a similarity floor
every row in the corpus collects near-equal credit and the search returns the
whole corpus instead of the best few rows. The floor is therefore a correctness
knob, not a tuning nicety, and its right value depends on the corpus and the
embedding model - so it has to be measured, not guessed.

This script sweeps candidate floors over the evaluated question set and reports,
for each floor, how many rows came back and whether the expected evidence
survived. Each question is embedded exactly once and the cached vector is reused
across every floor, so the embedding cost of a sweep is the cost of one pass over
the questions.

The bearer token is read from EFDS_AGENT_BEARER_TOKEN and is never printed. With
no token the script calibrates the anonymous entry point, which carries the same
floor argument; a token switches it to the authenticated function.

Usage:
    EFDS_AGENT_BEARER_TOKEN=... python scripts/calibrate_semantic_floor.py \
        --cases evals/cases/agent_preterm_live.json \
        --floors 0.20 0.25 0.30 0.35 0.40 0.45 0.50

The recommendation is the highest floor that keeps the best observed hit rate,
because a floor above that starts discarding evidence the search had already
found.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Any

import _bootstrap  # noqa: F401

from efds_agent.config import get_settings
from efds_agent.retrieval.embeddings import QueryEmbedder
from efds_agent.retrieval.modes import SOURCE_MODE_TYPES, SourceMode
from efds_agent.retrieval.supabase import DataAccessError, SupabaseRestClient
from efds_agent.security.authorization import AuthError, SupabaseAuthorization
from efds_agent.security.scopes import AgentScope, ScopeError

RPC_MULTI = "search_retrieval_units_multi"
RPC_PUBLIC = "search_retrieval_units_public"
RPC_PROFILE = "retrieval_embedding_profile"
DEFAULT_FLOORS = (0.20, 0.25, 0.30, 0.35, 0.40, 0.45, 0.50)


def parse_floors(raw: Sequence[str]) -> list[float]:
    """Validate, de-duplicate and sort the candidate floors."""
    floors: list[float] = []
    for item in raw:
        try:
            value = float(item)
        except ValueError as exc:
            raise SystemExit(f"Not a number: {item!r}") from exc
        if not 0.0 <= value <= 1.0:
            raise SystemExit(f"Floor out of range 0..1: {value}")
        if value not in floors:
            floors.append(value)
    if not floors:
        raise SystemExit("At least one floor is required")
    return sorted(floors)


def load_questions(path: Path) -> list[dict[str, Any]]:
    """Read the evaluated questions, tolerating both case-file schemas.

    A case file is a list of objects. The live and holdout sets carry the
    question plus the evidence that should be reachable; a bare list of strings
    is accepted too, for ad-hoc sweeps.
    """
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit(f"Could not read cases from {path}: {exc}") from exc
    if isinstance(payload, dict):
        payload = payload.get("cases", [])
    if not isinstance(payload, list) or not payload:
        raise SystemExit(f"No questions found in {path}")
    questions: list[dict[str, Any]] = []
    for entry in payload:
        if isinstance(entry, str):
            questions.append({"question": entry, "expected_source_types": []})
        elif isinstance(entry, dict) and str(entry.get("question") or "").strip():
            questions.append(entry)
    if not questions:
        raise SystemExit(f"No usable questions found in {path}")
    return questions


def row_matches_case(row: dict[str, Any], case: dict[str, Any]) -> bool:
    """Decide whether one returned row is evidence the case expected.

    Source type is the reliable field: both case schemas name the source types
    that should be reachable, and fixture ids in the holdout set do not
    correspond to real rows. Expected claims are checked as a fallback because
    they are substrings of the passage text rather than identifiers.
    """
    expected_types = {str(item) for item in (case.get("expected_source_types") or [])}
    row_type = str(row.get("source_type") or "")
    if expected_types:
        return row_type in expected_types
    haystack = " ".join(str(row.get(field) or "") for field in ("title", "content", "snippet")).lower()
    claims = [str(item).lower() for item in (case.get("expected_claims") or [])]
    return bool(claims) and any(claim in haystack for claim in claims)


def summarise(
    floor: float,
    row_counts: Sequence[int],
    hits: Sequence[bool],
    result_limit: int,
) -> dict[str, Any]:
    """Reduce one sweep into the numbers the decision is made from."""
    total = len(row_counts)
    if not total:
        return {
            "floor": floor,
            "questions": 0,
            "mean_rows": 0.0,
            "max_rows": 0,
            "saturated": 0,
            "empty": 0,
            "hits": 0,
            "hit_rate": 0.0,
        }
    return {
        "floor": floor,
        "questions": total,
        "mean_rows": round(sum(row_counts) / total, 2),
        "max_rows": max(row_counts),
        # A floor low enough to fill the window is not filtering: the fused
        # ranking is returning whatever it can, which is the failure the floor
        # exists to prevent.
        "saturated": sum(1 for count in row_counts if count >= result_limit),
        "empty": sum(1 for count in row_counts if count == 0),
        "hits": sum(1 for hit in hits if hit),
        "hit_rate": round(sum(1 for hit in hits if hit) / total, 4),
    }


def recommend(summaries: Sequence[dict[str, Any]]) -> dict[str, Any] | None:
    """Pick the highest floor that keeps the best hit rate.

    Highest rather than lowest because the point of the floor is to discard weak
    semantic matches; once a floor starts losing evidence, raising it further
    only loses more. Returns None when no floor found any expected evidence, in
    which case the corpus or the expected-evidence mapping is the problem and no
    floor will fix it.
    """
    usable = [item for item in summaries if item["questions"]]
    if not usable:
        return None
    best_rate = max(item["hit_rate"] for item in usable)
    if best_rate <= 0.0:
        return None
    return max((item for item in usable if item["hit_rate"] == best_rate), key=lambda item: item["floor"])


def _payload(
    query_vector: list[float],
    queries: Sequence[str],
    mode: SourceMode,
    floor: float,
    result_limit: int,
    authenticated: bool,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "search_queries": list(queries),
        "query_embedding": query_vector,
        "requested_source_types": list(SOURCE_MODE_TYPES[mode]),
        "requested_area": None,
        "requested_topic": None,
        "requested_channel": None,
        "requested_author": None,
        "requested_from": None,
        "requested_to": None,
        "requested_provider": None,
        "requested_model": None,
        "requested_model_version": None,
        "semantic_floor": float(floor),
        "result_limit": result_limit,
        "result_offset": 0,
    }
    if authenticated:
        payload["include_history"] = False
    return payload


async def _indexed_profile(client: SupabaseRestClient) -> dict[str, Any]:
    rows = await client.rpc(RPC_PROFILE, {})
    return dict(rows[0]) if rows else {}


def _print_table(summaries: Iterable[dict[str, Any]], result_limit: int) -> None:
    print(f"\n{'floor':>6}  {'rows':>5}  {'max':>4}  {'full':>5}  {'empty':>5}  {'hits':>5}  {'hit rate':>8}")
    for item in summaries:
        print(
            f"{item['floor']:>6.2f}  {item['mean_rows']:>5}  {item['max_rows']:>4}  "
            f"{item['saturated']:>5}  {item['empty']:>5}  {item['hits']:>5}  {item['hit_rate']:>8.2%}"
        )
    print(
        f"\n'full' counts questions that returned the whole {result_limit}-row window, which means the floor "
        "filtered nothing."
    )


async def run(args: argparse.Namespace) -> int:
    settings = get_settings()
    if not settings.is_supabase_configured:
        raise SystemExit("SUPABASE_URL and a publishable/anon key are required")
    if not settings.ai_api_key:
        raise SystemExit("AI_API_KEY is required: each question must be embedded before it can be ranked")

    token = os.getenv("EFDS_AGENT_BEARER_TOKEN")
    authorization = SupabaseAuthorization(settings)
    try:
        # Validate the token and the requested scope before any embedding call is
        # paid for. The result is not needed again: whether a token was supplied
        # already decides which retrieval function this sweep talks to.
        if token:
            await authorization.authenticate(token, AgentScope(args.scope))
        else:
            authorization.public(AgentScope.PUBLIC)
    except (AuthError, ScopeError) as exc:
        raise SystemExit(str(exc)) from exc

    client = SupabaseRestClient(settings, bearer_token=token)
    try:
        profile = await _indexed_profile(client)
    except DataAccessError as exc:
        raise SystemExit(f"Could not read the embedding profile: {exc}") from exc
    indexed_model = str(profile.get("model") or "")
    embedder = QueryEmbedder(settings)
    if not indexed_model:
        raise SystemExit("retrieval_embedding_profile returned no model, so the semantic branch cannot be calibrated")
    if indexed_model != embedder.model:
        # Cosine distance between vectors from different models is meaningless.
        # The gateway skips the semantic branch in this state, so a floor sweep
        # would measure the lexical branch alone and recommend a value that does
        # nothing.
        raise SystemExit(
            f"Embedding model mismatch: index was built with {indexed_model!r}, "
            f"this environment embeds with {embedder.model!r}. Configure the matching model first."
        )

    questions = (
        load_questions(Path(args.cases))
        if args.cases
        else [{"question": query, "expected_source_types": []} for query in args.query]
    )
    if not questions:
        raise SystemExit("Provide --cases or at least one --query")

    mode = SourceMode(args.source_mode)
    authenticated = bool(token)
    rpc_name = RPC_MULTI if authenticated else RPC_PUBLIC
    if not authenticated:
        print("No EFDS_AGENT_BEARER_TOKEN set: calibrating the anonymous entry point.\n")

    counts: dict[float, list[int]] = {floor: [] for floor in args.floors}
    hits: dict[float, list[bool]] = {floor: [] for floor in args.floors}
    for index, case in enumerate(questions, start=1):
        question = str(case["question"])
        vector = await embedder.embed(question)
        if vector is None:
            # embed() returns None when embedding is unavailable, which the
            # retrieval path tolerates by dropping the semantic branch. A floor
            # sweep cannot: the floor governs that branch, so without a vector
            # the sweep would silently measure the lexical ranking alone.
            raise SystemExit(
                f"Embedding failed for question {index} ({question!r}), so the semantic branch cannot be measured."
            )
        for floor in args.floors:
            try:
                rows = await client.rpc(
                    rpc_name,
                    _payload(vector, [question], mode, floor, args.result_limit, authenticated),
                )
            except DataAccessError as exc:
                raise SystemExit(f"Retrieval RPC failed at floor {floor} on question {index}: {exc}") from exc
            counts[floor].append(len(rows))
            hits[floor].append(any(row_matches_case(row, case) for row in rows))

    summaries = [summarise(floor, counts[floor], hits[floor], args.result_limit) for floor in args.floors]
    report = {
        "rpc": rpc_name,
        "source_mode": mode.value,
        "scope": args.scope,
        "result_limit": args.result_limit,
        "indexed_embedding_model": indexed_model,
        "questions": len(questions),
        "current_floor": float(settings.semantic_floor),
        "floors": summaries,
    }
    pick = recommend(summaries)
    report["recommended_floor"] = pick["floor"] if pick else None

    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print(f"Retrieval function: {rpc_name}   questions: {len(questions)}   window: {args.result_limit}")
        print(f"Configured today: SEMANTIC_FLOOR={report['current_floor']}")
        _print_table(summaries, args.result_limit)
        if pick is None:
            print(
                "\nNo floor in this sweep returned expected evidence for any question. Widen the sweep or check "
                "the expected-evidence mapping before choosing a floor."
            )
            return 1
        print(
            f"\nRecommended: SEMANTIC_FLOOR={pick['floor']:.2f} "
            f"(hit rate {pick['hit_rate']:.0%}, {pick['saturated']} of {pick['questions']} questions filled "
            "the window)"
        )
    if pick is None:
        return 1
    if args.fail_on_mismatch and pick["floor"] != float(settings.semantic_floor):
        return 1
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--cases",
        default="evals/cases/agent_preterm_live.json",
        help="case file to take questions from (default: %(default)s)",
    )
    parser.add_argument("--query", action="append", default=[], help="ad-hoc question; repeatable, overrides --cases")
    parser.add_argument(
        "--floors",
        nargs="+",
        default=[str(item) for item in DEFAULT_FLOORS],
        help="candidate floors to sweep (default: %(default)s)",
    )
    parser.add_argument("--result-limit", type=int, default=10, help="rows requested per question (default: 10)")
    parser.add_argument(
        "--scope",
        choices=[item.value for item in AgentScope],
        default=AgentScope.COMMITTEE.value,
        help="scope to request when a bearer token is supplied (default: %(default)s)",
    )
    parser.add_argument(
        "--source-mode",
        choices=[item.value for item in SourceMode],
        default=SourceMode.PRETERM_KNOWLEDGE.value,
        help="retrieval source mode (default: %(default)s)",
    )
    parser.add_argument("--json", action="store_true", help="emit the report as JSON")
    parser.add_argument(
        "--fail-on-mismatch",
        action="store_true",
        help="exit non-zero when the recommendation differs from the configured floor",
    )
    args = parser.parse_args(argv)
    args.floors = parse_floors(args.floors)
    return asyncio.run(run(args))


if __name__ == "__main__":
    sys.exit(main())
