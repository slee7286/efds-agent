#!/usr/bin/env python
"""Run authorization and retrieval against the configured Supabase project.

This command never invokes a model.  Supply a short-lived access token through
EFDS_AGENT_BEARER_TOKEN in the process environment; it is never read back or
printed by this script.
"""

import argparse
import asyncio
import json
import os

import _bootstrap  # noqa: F401

from efds_agent.agent.orchestrator import AgentOrchestrator
from efds_agent.config import get_settings
from efds_agent.retrieval.gateway import RetrievalDependencyError
from efds_agent.retrieval.modes import SourceMode
from efds_agent.security.authorization import AuthError, SupabaseAuthorization
from efds_agent.security.scopes import AgentScope, ScopeError


async def run(args: argparse.Namespace) -> int:
    requested_scope = AgentScope(args.scope)
    settings = get_settings()
    token = os.getenv("EFDS_AGENT_BEARER_TOKEN")
    authorization = SupabaseAuthorization(settings)
    try:
        if token:
            auth = await authorization.authenticate(token, requested_scope)
        elif requested_scope is AgentScope.PUBLIC:
            auth = authorization.public(requested_scope)
        else:
            raise SystemExit("Private smoke tests require EFDS_AGENT_BEARER_TOKEN")
    except (AuthError, ScopeError) as exc:
        raise SystemExit(str(exc)) from exc

    orchestrator = AgentOrchestrator(settings)
    try:
        plan, context, results = await orchestrator.retrieve(args.query, auth, source_mode=SourceMode(args.source_mode))
    except RetrievalDependencyError as exc:
        print(json.dumps({"status": "integration_failure", "category": "retrieval_dependency",
                          "message": str(exc), "query": args.query}, indent=2))
        return 2
    payload: dict[str, object] = {
        "authenticated_role": auth.access_role.value if auth.access_role else None,
        "requested_scope": requested_scope.value,
        "source_mode": args.source_mode,
        "effective_scope": auth.scope.effective_scope.value,
        "query": args.query,
        "plan": plan.model_dump(mode="json") if args.show_plan else None,
        "sources_searched": [result.source for result in results if result.queried],
        "result_counts": {result.source: len(result.evidence) for result in results},
        "citation_ids": [citation.id for citation in context.citations],
    }
    if args.show_results:
        payload["results"] = [
            {
                "source": result.source,
                "queried": result.queried,
                "note": result.note,
                "evidence": [
                    {
                        "source_type": item.citation.source_type,
                        "source_id": item.citation.source_id,
                        "title": item.citation.title,
                        "excerpt": item.citation.excerpt,
                    }
                    for item in result.evidence
                ],
            }
            for result in results
        ]
    if args.show_citations:
        payload["citations"] = [citation.model_dump(mode="json") for citation in context.citations]
    print(json.dumps(payload, indent=2, default=str))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="No-LLM live EFDS authorization/retrieval smoke test")
    parser.add_argument("--query", default="What do I need to do before inviting an external speaker?")
    parser.add_argument("--scope", choices=[item.value for item in AgentScope], default="public")
    parser.add_argument("--source-mode", choices=[item.value for item in SourceMode], default=SourceMode.PRETERM_KNOWLEDGE.value)
    parser.add_argument("--show-plan", action="store_true")
    parser.add_argument("--show-results", action="store_true")
    parser.add_argument("--show-citations", action="store_true")
    return asyncio.run(run(parser.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
