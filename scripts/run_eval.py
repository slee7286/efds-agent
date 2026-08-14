import argparse
import asyncio
import json
import os
import time
from pathlib import Path

import _bootstrap  # noqa: F401

from efds_agent.agent.orchestrator import AgentOrchestrator
from efds_agent.config import get_settings
from efds_agent.security.authorization import AuthError, SupabaseAuthorization, development_context
from efds_agent.security.scopes import AgentScope, ScopeError


async def run(path: Path, retrieval_only: bool, live: bool) -> dict[str, object]:
    cases = []
    for item in sorted(path.glob("*.json")):
        payload = json.loads(item.read_text(encoding="utf-8"))
        cases.extend(payload if isinstance(payload, list) else [payload])
    settings = get_settings()
    orchestrator = AgentOrchestrator(settings)
    token = os.getenv("EFDS_AGENT_BEARER_TOKEN")
    authorization = SupabaseAuthorization(settings)
    metrics: dict[str, object] = {
        "cases": len(cases), "authorized_cases": 0, "auth_denied": 0,
        "retrieval_hit": 0, "expected_source_hit": 0, "expected_source_type_hit": 0,
        "expected_source_id_hit": 0, "candidate_expected_source_id_hit": 0, "citation_presence": 0,
        "citation_validation_cases": 0, "citation_validity": 0, "forbidden_source_leakage": 0, "answer_support": 0,
        "failure_categories": {},
    }

    def record_failure(category: str) -> None:
        categories = metrics["failure_categories"]
        assert isinstance(categories, dict)
        categories[category] = int(categories.get(category, 0)) + 1

    for case in cases:
        requested_scope = AgentScope(case["scope"])
        started = time.perf_counter()
        try:
            if live:
                if not token:
                    raise AuthError("live evaluation requires EFDS_AGENT_BEARER_TOKEN")
                auth = await authorization.authenticate(token, requested_scope)
            else:
                auth = development_context(requested_scope)
            metrics["authorized_cases"] += 1
        except (AuthError, ScopeError) as exc:
            metrics["auth_denied"] += 1
            print(json.dumps({"id": case["id"], "status": "auth_denied", "error": str(exc)}))
            continue

        plan, context, results = await orchestrator.retrieve(case["question"], auth)
        citations = context.citations
        source_types = {citation.source_type for citation in citations}
        source_ids = {citation.source_id for citation in citations}
        result_counts = {item.source: len(item.evidence) for item in results}
        candidate_source_ids = [item.citation.source_id for result in results for item in result.evidence[:8]]
        if citations:
            metrics["retrieval_hit"] += 1
        expected_types = set(case.get("expected_source_types", []))
        expected_type_hit = not expected_types or bool(source_types & expected_types)
        if expected_type_hit:
            metrics["expected_source_hit"] += 1
            metrics["expected_source_type_hit"] += 1
        if case.get("citation_required") and citations:
            metrics["citation_presence"] += 1
        forbidden = set(case.get("forbidden_source_types", []))
        if source_types & forbidden:
            metrics["forbidden_source_leakage"] += 1
        expected_ids = set(case.get("expected_source_ids", []))
        stable_id_hit = not expected_ids or bool(source_ids & expected_ids)
        candidate_expected_id_hit = not expected_ids or bool(set(candidate_source_ids) & expected_ids)
        if stable_id_hit:
            metrics["expected_source_id_hit"] += 1
        if candidate_expected_id_hit:
            metrics["candidate_expected_source_id_hit"] += 1
        if citations and stable_id_hit:
            failure_category = "pass"
        elif citations and expected_types and not (source_types & expected_types):
            failure_category = "wrong_source_type"
        elif citations and expected_ids:
            failure_category = "expected_source_id_not_retrieved"
        elif auth.development_simulation and requested_scope is not AgentScope.PUBLIC:
            failure_category = "private_scope_simulation_without_bearer"
        elif not any(item.queried for item in results):
            failure_category = "no_authorized_adapter"
        else:
            failure_category = "no_matching_evidence"
        record_failure(failure_category)
        answer = ""
        if not retrieval_only:
            result = await orchestrator.answer(case["question"], auth)
            answer = result.answer
            if result.citations:
                metrics["citation_validation_cases"] += 1
                if all(citation.id in answer for citation in result.citations):
                    metrics["citation_validity"] += 1
            expected = [value.lower() for value in case.get("expected_answer_contains", [])]
            if (not expected or all(value in answer.lower() for value in expected)) and stable_id_hit:
                metrics["answer_support"] += 1
        else:
            if citations:
                metrics["citation_validation_cases"] += 1
                metrics["citation_validity"] += 1 if all(item.id for item in citations) else 0
        print(json.dumps({"id": case["id"], "scope": auth.scope.effective_scope.value, "plan": plan.model_dump(mode="json"), "sources": [item.source for item in results if item.queried], "result_counts": result_counts, "candidate_source_ids": candidate_source_ids, "citation_ids": [item.source_id for item in citations], "stable_id_hit": stable_id_hit, "candidate_expected_id_hit": candidate_expected_id_hit, "failure_category": failure_category, "latency_ms": round((time.perf_counter() - started) * 1000, 2), "answer": answer}, default=str))
    return metrics


def main() -> int:
    parser = argparse.ArgumentParser(description="Run corpus/evidence evaluations")
    parser.add_argument("--cases", type=Path, default=Path("evals/cases"))
    parser.add_argument("--retrieval-only", action="store_true", help="do not invoke a model provider")
    parser.add_argument("--live", action="store_true", help="use a real Supabase bearer token and RLS")
    args = parser.parse_args()
    print(json.dumps(asyncio.run(run(args.cases, args.retrieval_only, args.live)), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
