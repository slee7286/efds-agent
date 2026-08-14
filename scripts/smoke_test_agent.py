#!/usr/bin/env python
"""Opt-in live end-to-end smoke test: Supabase Auth/RLS -> RPC -> OpenAI."""

import argparse
import asyncio
import os
import time

import _bootstrap  # noqa: F401

from efds_agent.agent.orchestrator import AgentOrchestrator
from efds_agent.config import get_settings
from efds_agent.retrieval.gateway import RetrievalDependencyError
from efds_agent.retrieval.modes import SourceMode
from efds_agent.security.authorization import AuthError, SupabaseAuthorization
from efds_agent.security.scopes import AgentScope, ScopeError


async def run(args: argparse.Namespace) -> int:
    token = os.getenv("EFDS_AGENT_BEARER_TOKEN")
    if not token:
        raise SystemExit("Set EFDS_AGENT_BEARER_TOKEN to a short-lived Supabase access token")
    settings = get_settings()
    try:
        auth = await SupabaseAuthorization(settings).authenticate(token, AgentScope(args.scope))
    except (AuthError, ScopeError) as exc:
        raise SystemExit(str(exc)) from exc
    started = time.perf_counter()
    try:
        result = await AgentOrchestrator(settings).answer(args.query, auth, source_mode=SourceMode(args.source_mode))
    except RetrievalDependencyError as exc:
        raise SystemExit(f"retrieval dependency unavailable: {exc}") from exc
    print("answer:\n" + result.answer)
    print("citations:")
    for citation in result.citations:
        print(f"- [{citation.id}] {citation.source_type}: {citation.title} ({citation.retrieval_unit_id})")
    print(f"retrieval_quality: {result.retrieval_quality}")
    print(f"insufficient_evidence: {result.insufficient_evidence}")
    print(f"latency_ms: {round((time.perf_counter() - started) * 1000, 2)}")
    print(f"model_latency_ms: {result.trace.phase_latency_ms.get('model', 0)}")
    print(f"tokens: {result.trace.total_tokens}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Opt-in live EFDS agent smoke test")
    parser.add_argument("--query", default="What do we need to do before inviting an external speaker?")
    parser.add_argument("--scope", choices=[item.value for item in AgentScope if item is not AgentScope.PUBLIC], default="committee")
    parser.add_argument("--source-mode", choices=[item.value for item in SourceMode], default=SourceMode.PRETERM_KNOWLEDGE.value)
    return asyncio.run(run(parser.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
