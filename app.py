"""ASGI entrypoint for a separate Vercel Python service."""

from efds_agent.api.app import app

__all__ = ["app"]
