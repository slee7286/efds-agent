#!/usr/bin/env python
import argparse
import asyncio
import json
import os

import _bootstrap  # noqa: F401

from efds_agent.agent.orchestrator import AgentOrchestrator
from efds_agent.config import get_settings
from efds_agent.providers.factory import build_provider
from efds_agent.security.authorization import AuthError, SupabaseAuthorization, development_context
from efds_agent.security.scopes import AgentScope, ScopeError


async def run(args: argparse.Namespace) -> int:
    scope = AgentScope(args.scope)
    settings = get_settings()
    orchestrator = AgentOrchestrator(settings, build_provider(settings))
    token = os.getenv("EFDS_AGENT_BEARER_TOKEN")
    try:
        if args.dev_scope:
            auth = development_context(scope)
        elif token:
            auth = await SupabaseAuthorization(settings).authenticate(token, scope)
        elif scope is AgentScope.PUBLIC:
            auth = SupabaseAuthorization(settings).public(scope)
        else:
            raise SystemExit("Private CLI scopes require EFDS_AGENT_BEARER_TOKEN or explicit --dev-scope")
    except (AuthError, ScopeError) as exc:
        raise SystemExit(str(exc)) from exc
    result = await orchestrator.answer(args.question, auth)
    if args.show_plan or args.show_sources:
        print(json.dumps({"answer": result.answer, "scope": result.scope, "plan": result.plan.model_dump(mode="json"), "citations": [item.model_dump(mode="json") for item in result.citations], "trace": result.trace.model_dump(mode="json")}, indent=2, default=str))
    else:
        print(result.answer)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Ask the read-only EFDS agent")
    parser.add_argument("question")
    parser.add_argument("--scope", choices=[item.value for item in AgentScope], default="public")
    parser.add_argument("--dev-scope", action="store_true", help="simulate a scope locally; never accepted by HTTP")
    parser.add_argument("--show-sources", action="store_true")
    parser.add_argument("--show-plan", action="store_true")
    return asyncio.run(run(parser.parse_args()))


if __name__ == "__main__": raise SystemExit(main())
