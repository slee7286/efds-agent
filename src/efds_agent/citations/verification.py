"""Post-generation citation verification.

The model's own citations are a claim about its answer, not a guarantee. This
module checks each cited claim against the exact source spans it cites, and
retracts what does not hold up.

Expectations are set deliberately low. Attribution benchmarking shows that even
fine-tuned verifiers reach only around 80% macro-F1 on binary attribution, so a
verifier is a tripwire, not an oracle. Consequences:

- Only an explicit "unsupported" verdict is acted on. Ambiguity is left alone.
- An unsupported citation is removed rather than silently rewritten, and the
  removal is reported in the response limitations, so a reader is never told
  that something was verified when it was only unrefuted.
- When every cited claim fails, the answer is withheld and treated as
  insufficient evidence. Refusal is a first-class outcome.

Verification failure is non-fatal: if the verifier is unavailable the answer is
returned unchanged, and the trace records that verification did not run.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any

from efds_agent.citations.models import Citation, Evidence
from efds_agent.providers.base import GenerationRequest, ModelProvider, TaskType

logger = logging.getLogger(__name__)

MAX_CITED_SOURCES = 12
MAX_SPAN_CHARS = 1200

VERIFICATION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "unsupported": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "claim": {"type": "string"},
                    "citation_id": {"type": "string"},
                    "reason": {"type": "string"},
                },
                "required": ["claim", "citation_id", "reason"],
                "additionalProperties": False,
            },
        },
        "all_supported": {"type": "boolean"},
    },
    "required": ["unsupported", "all_supported"],
    "additionalProperties": False,
}

VERIFICATION_INSTRUCTIONS = """You audit citations in an answer against the sources it cites.

You are given numbered source excerpts and an answer that cites them by identifier such as [S2]. For each citation, decide whether the source it points to actually supports the sentence the citation is attached to.

Mark a citation as unsupported only when the cited source clearly does not support the claim: the claim is about something absent from the source, the source says the opposite, the citation points at the wrong source, or the source is too vague to establish the specific fact asserted. Do not mark a citation unsupported merely because it is brief, partial, or could be clearer, and do not require the source to use the answer's exact wording. If the source plausibly supports the claim, leave it alone.

Report one entry in "unsupported" for each citation that fails, quoting the claim and naming the citation identifier. Set all_supported to true when nothing fails."""

_CITATION_RE = re.compile(r"\[(S\d+)\]")


@dataclass
class VerificationOutcome:
    """What the verifier changed, and what it cost."""

    answer: str
    verified: bool = False
    checked_citations: int = 0
    removed_citations: list[str] = field(default_factory=list)
    unsupported_claims: list[str] = field(default_factory=list)
    withheld: bool = False
    note: str | None = None


def _source_spans(answer: str, evidence: list[Evidence]) -> tuple[str, list[str]]:
    cited = {match for match in _CITATION_RE.findall(answer)}
    if not cited:
        return "", []
    by_id = {item.citation.id: item for item in evidence}
    blocks: list[str] = []
    included: list[str] = []
    for citation_id in sorted(cited, key=lambda value: int(value[1:])):
        item = by_id.get(citation_id)
        if item is None:
            continue
        if len(included) >= MAX_CITED_SOURCES:
            break
        span = item.text[:MAX_SPAN_CHARS]
        blocks.append(f"[{citation_id}] {item.citation.title}\n{span}")
        included.append(citation_id)
    return "\n\n".join(blocks), included


def _parse(payload: str, allowed: set[str]) -> tuple[list[dict[str, str]], bool] | None:
    try:
        data = json.loads(payload)
    except (TypeError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    raw = data.get("unsupported")
    findings: list[dict[str, str]] = []
    if isinstance(raw, list):
        for entry in raw:
            if not isinstance(entry, dict):
                continue
            citation_id = str(entry.get("citation_id") or "").strip()
            # A verdict about a citation that does not exist in this answer is
            # discarded: it cannot be acted on safely.
            if citation_id not in allowed:
                continue
            findings.append(
                {
                    "citation_id": citation_id,
                    "claim": str(entry.get("claim") or "").strip(),
                    "reason": str(entry.get("reason") or "").strip(),
                }
            )
    return findings, bool(data.get("all_supported"))


async def verify_citations(
    answer: str,
    evidence: list[Evidence],
    citations: list[Citation],
    provider: ModelProvider,
    *,
    enabled: bool = True,
) -> VerificationOutcome:
    """Check cited claims against their sources and retract what fails."""
    if not enabled:
        return VerificationOutcome(answer=answer, verified=False, note="verification_disabled")
    if not answer.strip() or not citations:
        return VerificationOutcome(answer=answer, verified=False, note="nothing_to_verify")
    spans, included = _source_spans(answer, evidence)
    if not spans:
        return VerificationOutcome(answer=answer, verified=False, note="no_resolvable_citations")
    request = GenerationRequest(
        question=f"{spans}\n\nAnswer to audit:\n{answer}",
        system_prompt=VERIFICATION_INSTRUCTIONS,
        context="",
        task_type=TaskType.VERIFICATION,
        response_schema=VERIFICATION_SCHEMA,
        structured_output_name="citation_audit",
        max_output_tokens=700,
    )
    try:
        generated = await provider.generate(request)
    except Exception:  # noqa: BLE001 - verification is advisory; an outage must not fail the request
        logger.warning("citation verification failed; returning the answer unverified")
        return VerificationOutcome(answer=answer, verified=False, note="verifier_error")
    parsed = _parse(generated.answer, set(included))
    if parsed is None:
        return VerificationOutcome(answer=answer, verified=False, note="unparsable_verdict")
    findings, all_supported = parsed
    if not findings:
        return VerificationOutcome(
            answer=answer,
            verified=True,
            checked_citations=len(included),
            note="all_supported" if all_supported else None,
        )

    removed = list(dict.fromkeys(item["citation_id"] for item in findings))
    cleaned = answer
    for citation_id in removed:
        cleaned = cleaned.replace(f"[{citation_id}]", "")
    cleaned = re.sub(r"[ \t]{2,}", " ", cleaned)
    cleaned = re.sub(r" +([.,;:])", r"\1", cleaned).strip()

    remaining = {match for match in _CITATION_RE.findall(cleaned)}
    withheld = not remaining
    return VerificationOutcome(
        answer=cleaned,
        verified=True,
        checked_citations=len(included),
        removed_citations=removed,
        unsupported_claims=[item["claim"] for item in findings if item["claim"]],
        withheld=withheld,
        note="all_citations_unsupported" if withheld else "citations_removed",
    )
