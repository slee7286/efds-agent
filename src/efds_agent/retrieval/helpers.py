import re
from collections.abc import Iterable
from itertools import pairwise
from typing import Any

from efds_agent.citations.models import Citation, Evidence


def term_filter(terms: Iterable[str], columns: Iterable[str]) -> str:
    clean = [re.sub(r"[^A-Za-z0-9_-]", " ", term).strip() for term in terms if term.strip()]
    clean = list(dict.fromkeys(term for term in clean if term))[:8]
    if not clean:
        return ""
    # PostgREST's `or` expression is deliberately constructed from the
    # planner's tokenized terms; arbitrary filter expressions never reach the
    # database client.
    return "(" + ",".join(f"{column}.ilike.*{term}*" for term in clean for column in columns) + ")"


def lexical_relevance(values: Iterable[Any], terms: Iterable[str], base: float = 0.4) -> float:
    haystack = " ".join(text(value) for value in values).lower()
    clean_terms = list(dict.fromkeys(term.lower() for term in terms if term.strip()))
    if not clean_terms:
        return base
    matched = sum(term in haystack for term in clean_terms)
    return min(0.99, base + (matched / len(clean_terms)) * 0.45)


def source_article_relevance(title: Any, body: Any, terms: Iterable[str], base: float = 0.4) -> float:
    """Rank source articles by title/phrase matches before broad body matches."""
    clean_terms = list(dict.fromkeys(term.lower() for term in terms if term.strip()))
    if not clean_terms:
        return base
    title_text = text(title).lower()
    body_text = text(body).lower()
    title_words = set(re.findall(r"[a-z0-9][a-z0-9_-]*", title_text))
    title_matches = sum(term in title_words for term in clean_terms)
    body_matches = sum(term in body_text for term in clean_terms)
    score = base + (title_matches / len(clean_terms)) * 0.40 + (body_matches / len(clean_terms)) * 0.20
    if len(clean_terms) > 1:
        adjacent_pairs = pairwise(clean_terms)
        if any(f"{first} {second}" in title_text or f"{first} {second}" in body_text for first, second in adjacent_pairs):
            score += 0.15
    return min(0.99, score)


_ABSOLUTE_PATH = re.compile(r"(?i)(?:[a-z]:[\\/][^\s\n]+|\\\\[^\s\n]+)")


def redact_absolute_paths(value: str) -> str:
    return _ABSOLUTE_PATH.sub("[absolute path redacted]", value)


def safe_relative_path(value: Any) -> str | None:
    path = text(value).replace("\\", "/")
    if not path:
        return None
    if re.match(r"(?i)^[a-z]:/", path) or path.startswith("//"):
        return "[absolute path redacted]"
    return path.lstrip("/")


def text(value: Any, fallback: str = "") -> str:
    return str(value).strip() if value not in (None, "") else fallback


def evidence(citation: Citation, body: str, relevance: float = 0.6, metadata: dict[str, object] | None = None) -> Evidence | None:
    body = body.strip()
    if not body: return None
    citation.excerpt = body[:1200]
    return Evidence(citation=citation, text=body[:2400], relevance=relevance, review_status=citation.review_status, metadata=metadata or {})
