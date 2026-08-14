#!/usr/bin/env python
"""Run a live, retrieval-first agent evaluation against the real service.

The default is retrieval-only.  Add --with-model explicitly to call OpenAI.
Results contain IDs and bounded previews, never bearer tokens or full source
documents.  The script is intentionally conservative: answer quality still
requires manual rubric review.
"""

import argparse
import asyncio
import hashlib
import json
import os
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()[:16]


def _classify(case: dict[str, Any], response: dict[str, Any], *, with_model: bool) -> str:
    if response.get("integration_error"):
        return "INTEGRATION_FAILURE"
    expected = set(case.get("expected_evidence_ids", [])) | set(case.get("acceptable_evidence_ids", []))
    actual = {item.get("retrieval_unit_id") for item in response.get("evidence", [])}
    if expected and not expected.intersection(actual):
        return "RETRIEVAL_MISS"
    if case.get("expected_insufficient_evidence"):
        if with_model and not response.get("insufficient_evidence", False):
            return "ABSTENTION_FAILURE"
        return "PASS"
    if not with_model:
        return "PASS" if expected.intersection(actual) else "RETRIEVAL_MISS"
    invalid = response.get("invalid_citation_ids", [])
    if invalid:
        return "CITATION_MISS"
    required = case.get("required_answer_terms", [])
    answer = str(response.get("answer", "")).casefold()
    if required and not any(str(term).casefold() in answer for term in required):
        return "GENERATION_MISS"
    return "PASS"


async def run(args: argparse.Namespace) -> int:
    token = os.getenv("EFDS_AGENT_BEARER_TOKEN")
    if not token:
        raise SystemExit("Set EFDS_AGENT_BEARER_TOKEN to a short-lived Supabase access token")
    cases = json.loads(Path(args.cases).read_text(encoding="utf-8"))
    if not isinstance(cases, list):
        raise SystemExit("evaluation file must contain a JSON list")
    headers = {"Authorization": f"Bearer {token}"}
    if os.getenv("EFDS_AGENT_SHARED_SECRET"):
        headers["X-EFDS-Agent-Secret"] = os.environ["EFDS_AGENT_SHARED_SECRET"]
    output: dict[str, Any] = {
        "status": "completed", "started_at": datetime.now(UTC).isoformat(),
        "with_model": args.with_model, "service_url": args.agent_url,
        "cases": [],
    }
    async with httpx.AsyncClient(timeout=args.timeout) as client:
        for case in cases:
            query = str(case["query"])
            payload = {"query": query, "scope": case.get("scope", args.scope),
                       "source_mode": case.get("source_mode", "preterm_knowledge"),
                       "conversation": case.get("conversation", [])}
            endpoint = "/v1/query" if args.with_model else "/v1/retrieval"
            started = time.perf_counter()
            item: dict[str, Any] = {"id": case.get("id", _hash(query)), "query_hash": _hash(query),
                                    "endpoint": endpoint, "scope": payload["scope"], "source_mode": payload["source_mode"]}
            try:
                response = await client.post(args.agent_url.rstrip("/") + endpoint, headers=headers, json=payload)
                item["http_status"] = response.status_code
                body = response.json()
                if response.status_code >= 400:
                    body = {"integration_error": response.text[:240]}
                if args.with_model:
                    item.update({"answer": body.get("answer", ""), "citations": body.get("citations", []),
                                 "insufficient_evidence": body.get("insufficient_evidence", False),
                                 "invalid_citation_ids": body.get("trace", {}).get("invalid_citations_removed", []),
                                 "evidence": [{"retrieval_unit_id": c.get("retrieval_unit_id"), "title": c.get("title"),
                                               "source_type": c.get("source_type"), "preview": c.get("excerpt", "")[:240]}
                                              for c in body.get("citations", [])]})
                else:
                    item.update({"retrieval_quality": body.get("retrieval_quality"), "retrieval_metadata": body.get("retrieval_metadata", {}),
                                 "evidence": body.get("evidence", [])})
            except (httpx.HTTPError, ValueError) as exc:
                item["integration_error"] = type(exc).__name__
            item["latency_ms"] = round((time.perf_counter() - started) * 1000, 2)
            item["classification"] = _classify(case, item, with_model=args.with_model)
            output["cases"].append(item)
    counts: dict[str, int] = {}
    for item in output["cases"]:
        label = item["classification"]
        counts[label] = counts.get(label, 0) + 1
    output["classification_counts"] = counts
    output["finished_at"] = datetime.now(UTC).isoformat()
    Path(args.output).parent.mkdir(parents=True, exist_ok=True)
    Path(args.output).write_text(json.dumps(output, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"output": args.output, "classification_counts": counts}, indent=2))
    return 0 if not counts.get("INTEGRATION_FAILURE") else 2


def main() -> int:
    parser = argparse.ArgumentParser(description="Opt-in live EFDS agent evaluation")
    parser.add_argument("--agent-url", default="http://127.0.0.1:8000")
    parser.add_argument("--cases", default="evals/cases/agent_preterm_live.json")
    parser.add_argument("--output", default="evaluation/agent_v1_live_eval_results.json")
    parser.add_argument("--scope", choices=["public", "viewer", "member", "committee", "admin"], default="committee")
    parser.add_argument("--timeout", type=float, default=60)
    parser.add_argument("--with-model", action="store_true")
    return asyncio.run(run(parser.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
