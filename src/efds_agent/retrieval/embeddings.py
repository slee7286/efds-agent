"""Query embedding for semantic retrieval.

The agent must embed queries with the same provider, model and version that
produced the stored passage embeddings; comparing vectors from different models
is meaningless. `retrieval_embedding_profile()` exposes the profile the index
was actually built with, and the gateway refuses to send a vector when that
profile disagrees with the configured embedding model.
"""

from __future__ import annotations

import logging
from collections import OrderedDict
from typing import Any

logger = logging.getLogger(__name__)

# Query embeddings are deterministic, so an exact-match cache is safe and
# removes the latency and cost of re-embedding repeated questions. Semantic
# (approximate) caching is deliberately not used: at this call volume the
# saving is negligible next to the risk of a confidently wrong cache hit.
_CACHE_LIMIT = 512


class QueryEmbedder:
    """Embed retrieval queries with bounded in-process caching."""

    def __init__(self, settings: Any, client: Any | None = None) -> None:
        self.settings = settings
        self._client = client
        self._cache: OrderedDict[tuple[str, int, str], list[float]] = OrderedDict()

    @property
    def model(self) -> str:
        return str(self.settings.embedding_model)

    @property
    def dimensions(self) -> int:
        return int(self.settings.embedding_dimensions)

    def _client_instance(self) -> Any:
        if self._client is not None:
            return self._client
        from openai import AsyncOpenAI

        api_key = self.settings.ai_api_key
        if not api_key:
            raise RuntimeError("AI_API_KEY is required to embed queries")
        self._client = AsyncOpenAI(
            api_key=api_key.get_secret_value(),
            timeout=self.settings.request_timeout_seconds,
            max_retries=0,
        )
        return self._client

    def _cache_get(self, key: tuple[str, int, str]) -> list[float] | None:
        found = self._cache.get(key)
        if found is not None:
            self._cache.move_to_end(key)
        return found

    def _cache_put(self, key: tuple[str, int, str], value: list[float]) -> None:
        self._cache[key] = value
        self._cache.move_to_end(key)
        while len(self._cache) > _CACHE_LIMIT:
            self._cache.popitem(last=False)

    async def embed(self, text: str) -> list[float] | None:
        """Return the query vector, or None when embedding is unavailable.

        A None result disables only the semantic branch of retrieval; lexical
        retrieval continues to work. Retrieval must never fail because an
        optional enhancement is down.
        """
        clean = text.strip()
        if not clean:
            return None
        model, dimensions = self.model, self.dimensions
        key = (model, dimensions, clean)
        cached = self._cache_get(key)
        if cached is not None:
            return cached
        try:
            response = await self._client_instance().embeddings.create(
                model=model, input=[clean], dimensions=dimensions
            )
        except Exception:  # noqa: BLE001 - an embedding outage only disables the semantic branch
            logger.warning("query embedding failed; semantic retrieval disabled for this request")
            return None
        data = getattr(response, "data", None)
        if not data:
            return None
        vector = getattr(data[0], "embedding", None)
        if not isinstance(vector, list) or not vector:
            return None
        floats = [float(value) for value in vector]
        self._cache_put(key, floats)
        return floats
