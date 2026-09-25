#!/usr/bin/env python
"""Compare KB canonical output with the agent's exact retrieval HTTP path."""

import argparse
import asyncio
import json
import os
from pathlib import Path
from typing import Any

import _bootstrap  # noqa: F401
import httpx


def _compare(canonical: dict[str, Any], actual: dict[str, Any]) -> dict[str, Any]:
    expected = [row["retrieval_unit_id"] for row in canonical.get("results", [])]
    received = [row["retrieval_unit_id"] for row in actual.get("evidence", [])]
    return {
        "query": canonical.get("query"),
        "expected_count": len(expected),
        "actual_count": len(received),
        "exact_order_match": expected == received,
        "top10_set_match": set(expected[:10]) == set(received[:10]),
        "missing_ids": [value for value in expected if value not in received],
        "unexpected_ids": [value for value in received if value not in expected],
        "rank_differences": [
            {
                "retrieval_unit_id": value,
                "canonical_rank": expected.index(value) + 1,
                "agent_rank": received.index(value) + 1,
            }
            for value in expected
            if value in received and expected.index(value) != received.index(value)
        ],
    }


async def run(args: argparse.Namespace) -> int:
    token = os.getenv("EFDS_AGENT_BEARER_TOKEN")
    if not token:
        raise SystemExit("Set EFDS_AGENT_BEARER_TOKEN to a short-lived Supabase access token")
    canonical = json.loads(Path(args.canonical).read_text(encoding="utf-8"))
    headers = {"Authorization": f"Bearer {token}"}
    if os.getenv("EFDS_AGENT_SHARED_SECRET"):
        headers["X-EFDS-Agent-Secret"] = os.environ["EFDS_AGENT_SHARED_SECRET"]
    report: dict[str, Any] = {"status": "completed", "contract": "agent_retrieval_parity_v1", "cases": []}
    async with httpx.AsyncClient(timeout=args.timeout) as client:
        for case in canonical.get("cases", []):
            payload = {
                "query": case["query"],
                "scope": case.get("scope", args.scope),
                "source_mode": args.source_mode,
                "conversation": [],
            }
            try:
                response = await client.post(
                    args.agent_url.rstrip("/") + "/v1/retrieval", headers=headers, json=payload
                )
                body = response.json()
                if response.status_code >= 400:
                    report["cases"].append(
                        {
                            "query": case["query"],
                            "status": "INTEGRATION_FAILURE",
                            "http_status": response.status_code,
                            "detail": body.get("detail", "agent retrieval failed"),
                        }
                    )
                else:
                    item = _compare(case, body)
                    item["status"] = "PASS" if item["exact_order_match"] else "PARITY_MISMATCH"
                    report["cases"].append(item)
            except (httpx.HTTPError, ValueError) as exc:
                report["cases"].append(
                    {"query": case.get("query"), "status": "INTEGRATION_FAILURE", "detail": type(exc).__name__}
                )
    exact = sum(item.get("exact_order_match", False) for item in report["cases"])
    report["exact_order_match_rate"] = exact / len(report["cases"]) if report["cases"] else 0.0
    report["top10_set_match_rate"] = (
        sum(item.get("top10_set_match", False) for item in report["cases"]) / len(report["cases"])
        if report["cases"]
        else 0.0
    )
    Path(args.output).parent.mkdir(parents=True, exist_ok=True)
    Path(args.output).write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                "output": args.output,
                "exact_order_match_rate": report["exact_order_match_rate"],
                "top10_set_match_rate": report["top10_set_match_rate"],
            },
            indent=2,
        )
    )
    return 0 if all(item.get("status") == "PASS" for item in report["cases"]) else 2


def main() -> int:
    parser = argparse.ArgumentParser(description="Compare KB export with agent retrieval")
    parser.add_argument("--canonical", required=True)
    parser.add_argument("--output", default="evaluation/agent_v1_retrieval_parity.json")
    parser.add_argument("--agent-url", default="http://127.0.0.1:8000")
    parser.add_argument("--scope", default="admin")
    parser.add_argument("--source-mode", default="preterm_knowledge")
    parser.add_argument("--timeout", type=float, default=60)
    return asyncio.run(run(parser.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
