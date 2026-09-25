import argparse
import json

import _bootstrap  # noqa: F401

from efds_agent.security.scopes import AgentScope, can_read_source


def main() -> int:
    parser = argparse.ArgumentParser(description="Show the V1 source/access contract")
    parser.add_argument("--scope", choices=[item.value for item in AgentScope], default="public")
    args = parser.parse_args()
    scope = AgentScope(args.scope)
    sources = ["knowledge_public", "knowledge", "documents_legacy", "documents_onedrive", "slack", "operational"]
    print(
        json.dumps(
            {
                "scope": scope.value,
                "readable": [source for source in sources if can_read_source(scope, source)],
                "tables_are_allowlisted": True,
                "service_role_used": False,
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
