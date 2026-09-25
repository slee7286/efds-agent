#!/usr/bin/env python
"""Read-only check for migration 0014's PostgREST retrieval contract.

This script never applies migrations.  A bearer token may be supplied for an
RLS-aware probe, but is never printed or persisted.
"""

import argparse
import asyncio
import json
import os

import _bootstrap  # noqa: F401

from efds_agent.config import get_settings
from efds_agent.retrieval.health import retrieval_contract_health
from efds_agent.retrieval.supabase import SupabaseRestClient


async def run(args: argparse.Namespace) -> int:
    settings = get_settings()
    client = SupabaseRestClient(settings, os.getenv("EFDS_AGENT_BEARER_TOKEN"))
    result = await retrieval_contract_health(client, probe=not args.no_probe)
    print(json.dumps(result, indent=2, sort_keys=True))
    if args.require_ready and result.get("status") != "ready":
        return 2
    if args.require_schema and not result.get("result_schema_verified", False):
        return 3
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Check the fixed EFDS retrieval RPC without applying migrations")
    parser.add_argument("--no-probe", action="store_true", help="only inspect the PostgREST schema document")
    parser.add_argument("--require-ready", action="store_true", help="exit 2 unless the RPC probe is ready")
    parser.add_argument(
        "--require-schema",
        action="store_true",
        help="exit 3 unless at least one authorized result validates the row schema",
    )
    return asyncio.run(run(parser.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
