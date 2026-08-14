from efds_agent.retrieval.router import _terms, build_plan
from efds_agent.security.authorization import development_context
from efds_agent.security.scopes import AgentScope


def test_process_question_routes_to_knowledge():
    plan = build_plan("What do I need to do before inviting an external speaker?", development_context(AgentScope.COMMITTEE))
    assert "knowledge" in plan.sources
    assert "requirement_lookup" in plan.intents
    assert "process_lookup" in plan.intents


def test_member_discussion_does_not_route_to_slack():
    plan = build_plan("What did we discuss in Slack about the careers fair?", development_context(AgentScope.MEMBER))
    assert "slack" not in plan.sources


def test_admin_discussion_routes_to_slack():
    plan = build_plan("What did we discuss in Slack about the careers fair?", development_context(AgentScope.ADMIN))
    assert "slack" in plan.sources


def test_query_terms_remove_generic_words_but_keep_domain_terms():
    terms = _terms("How much notice is required for the EFDS operating plan document?")
    assert terms == ["notice", "required", "efds", "operating", "plan", "document"]
