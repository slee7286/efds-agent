"""Shared retrieval-layer types.

These live apart from `gateway` so that lower-level modules (the query planner)
can depend on them without importing the gateway and creating a cycle.
"""

from __future__ import annotations

from pydantic import BaseModel


class ConversationTurn(BaseModel):
    """One prior conversational turn, already bounded by the caller."""

    role: str
    content: str
