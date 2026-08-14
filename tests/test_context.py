from efds_agent.agent.context import build_context
from efds_agent.citations.models import Citation, Evidence
from efds_agent.retrieval.router import QueryPlan
from efds_agent.security.scopes import AgentScope


def test_context_prefers_approved_structured_evidence_over_proposed_article_when_relevance_is_close():
    proposed = Evidence(citation=Citation(id="x", source_type="icu_article", source_id="article", title="Article", authority="icu_source", review_status="proposed"), text="source", relevance=0.8, review_status="proposed")
    approved = Evidence(citation=Citation(id="y", source_type="icu_requirement", source_id="requirement", title="Requirement", authority="approved_structured", review_status="approved"), text="structured", relevance=0.75, review_status="approved")
    package = build_context("question", QueryPlan(), [proposed, approved], AgentScope.COMMITTEE, max_items=1)
    assert package.items[0].citation.source_id == "requirement"
