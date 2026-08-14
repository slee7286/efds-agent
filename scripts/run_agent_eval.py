#!/usr/bin/env python
"""Deterministic agent evaluation; no live OpenAI call is made by default."""

import argparse
import asyncio
import json
from pathlib import Path
from typing import Any

import _bootstrap  # noqa: F401

from efds_agent.agent.context import build_context
from efds_agent.agent.orchestrator import AgentOrchestrator
from efds_agent.citations.models import Citation, Evidence
from efds_agent.config import Settings
from efds_agent.retrieval.gateway import RetrievalRequest
from efds_agent.retrieval.modes import SourceMode
from efds_agent.security.authorization import development_context
from efds_agent.security.scopes import AgentScope


class FixtureGateway:
    def __init__(self, case: dict[str, Any]):
        self.case = case

    async def retrieve(self, request: RetrievalRequest, auth):
        items = []
        for index, row in enumerate(self.case.get("evidence", []), start=1):
            citation = Citation(id=f"S{index}", retrieval_unit_id=str(row["retrieval_unit_id"]),
                                source_type=str(row["source_type"]), source_id=str(row["source_record_id"]),
                                source_record_id=str(row["source_record_id"]), title=str(row["title"]),
                                excerpt=str(row["snippet"]), authority=str(row.get("authority", "source")),
                                metadata={"visibility": row.get("visibility"), "source_area": row.get("source_area")})
            items.append(Evidence(citation=citation, text=str(row["snippet"]), relevance=float(row.get("score", 0.5))))
        # This fixture gateway models the RLS result, so internal evidence is
        # invisible to non-admin cases even though it is present in the test.
        if auth.scope.effective_scope is not AgentScope.ADMIN:
            items = [item for item in items if item.citation.metadata.get("visibility") != "internal"]
        return build_context(request.query, items, auth.scope.effective_scope, max_items=10, metadata={
            "retrieval_quality": "low" if not items else "normal", "source_mode": request.source_mode.value,
            "source_mode_certification": "beta", "source_family_count": len({i.citation.source_type for i in items})})


class FixtureProvider:
    name = "fixture"
    model = "fixture"

    def __init__(self, answer: str):
        self.answer = answer

    async def generate(self, request):
        from efds_agent.providers.base import GenerationResult
        return GenerationResult(answer=self.answer, model=self.model)

    async def stream(self, request):
        yield self.answer


async def run(path: Path) -> dict[str, Any]:
    cases = json.loads(path.read_text(encoding="utf-8"))
    metrics: dict[str, Any] = {"cases": len(cases), "passed": 0, "retrieval_miss": 0, "expected_retrieval_miss": 0, "generation_miss": 0,
                               "invalid_citation": 0, "insufficient_evidence_miss": 0, "authorization_leakage": 0,
                               "citation_validity": 0, "results": []}
    for case in cases:
        auth = development_context(AgentScope(case["scope"]))
        provider = FixtureProvider(case.get("mock_answer", ""))
        result = await AgentOrchestrator(Settings(_env_file=None), provider, gateway=FixtureGateway(case)).answer(
            case["question"], auth, source_mode=SourceMode.PRETERM_KNOWLEDGE)
        expected_ids = set(case.get("expected_source_ids", []))
        actual_ids = {citation.retrieval_unit_id for citation in result.citations}
        retrieval_miss = bool(expected_ids - actual_ids)
        if retrieval_miss:
            metrics["retrieval_miss"] += 1
            if case.get("expected_retrieval_miss"):
                metrics["expected_retrieval_miss"] += 1
        expected_insufficient = bool(case.get("expected_insufficient_evidence", False))
        insufficient_miss = result.insufficient_evidence != expected_insufficient
        if insufficient_miss:
            metrics["insufficient_evidence_miss"] += 1
        invalid = bool(result.trace.invalid_citations_removed)
        if invalid:
            metrics["invalid_citation"] += 1
        citation_valid = all(citation.id in {f"S{i}" for i in range(1, len(result.citations) + 1)} for citation in result.citations)
        if citation_valid:
            metrics["citation_validity"] += 1
        answer_lower = result.answer.lower()
        generation_miss = bool(result.citations) and any(claim.lower() not in answer_lower for claim in case.get("expected_claims", []))
        if generation_miss and not retrieval_miss:
            metrics["generation_miss"] += 1
        forbidden = set(case.get("forbidden_source_types", []))
        leakage = bool(forbidden & {citation.source_type for citation in result.citations})
        if leakage:
            metrics["authorization_leakage"] += 1
        unexpected_retrieval_miss = retrieval_miss and not case.get("expected_retrieval_miss", False)
        unexpected_generation_miss = generation_miss and not case.get("expected_generation_miss", False)
        unexpected_invalid = invalid and not case.get("expected_invalid_citation", False)
        passed = not unexpected_retrieval_miss and not insufficient_miss and not unexpected_generation_miss and not leakage and not unexpected_invalid
        if passed:
            metrics["passed"] += 1
        metrics["results"].append({"id": case["id"], "passed": passed,
                                   "failure_categories": (["RETRIEVAL_MISS"] if retrieval_miss else []) +
                                   (["INSUFFICIENT_EVIDENCE"] if insufficient_miss else []) +
                                   (["GENERATION_MISS"] if generation_miss else []) +
                                   (["INVALID_CITATION"] if invalid else []) +
                                   (["AUTHORIZATION_LEAKAGE"] if leakage else [])})
    return metrics


def main() -> int:
    parser = argparse.ArgumentParser(description="Run deterministic agent-level evaluations")
    parser.add_argument("--cases", type=Path, default=Path("evals/cases/agent_preterm_holdout.json"))
    args = parser.parse_args()
    print(json.dumps(asyncio.run(run(args.cases)), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
