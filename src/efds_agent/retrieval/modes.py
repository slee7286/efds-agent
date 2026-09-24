from enum import StrEnum


class SourceMode(StrEnum):
    PRETERM_KNOWLEDGE = "preterm_knowledge"
    FULL_INSTITUTIONAL = "full_institutional"
    COMMITTEE_TICKETS = "committee_tickets"
    ADMIN_OUTLOOK_TICKETS = "admin_outlook_tickets"
    PUBLIC = "public"


# This is a policy allow-list, not a ranking list. The returned order is owned
# by efds-knowledge-base. PRETERM intentionally narrows document access to the
# currently approved 01_governance area.
SOURCE_MODE_TYPES: dict[SourceMode, tuple[str, ...]] = {
    SourceMode.PRETERM_KNOWLEDGE: (
        "icu_article", "knowledge_requirement", "knowledge_timing_rule",
        "knowledge_process", "knowledge_process_step", "knowledge_resource",
        "knowledge_contact", "document", "operational_decision", "operational_action",
        "operational_commitment", "operational_question", "operational_status",
    ),
    SourceMode.FULL_INSTITUTIONAL: (
        "icu_article", "knowledge_requirement", "knowledge_timing_rule",
        "knowledge_process", "knowledge_process_step", "knowledge_resource",
        "knowledge_contact", "document", "slack_message", "outlook_message", "meeting_transcript",
        "meeting_summary", "meeting_notes", "operational_decision", "operational_action",
        "operational_commitment", "operational_question", "operational_status",
    ),
    SourceMode.COMMITTEE_TICKETS: ("slack_message",),
    SourceMode.ADMIN_OUTLOOK_TICKETS: ("outlook_message",),
    SourceMode.PUBLIC: (
        "knowledge_resource", "knowledge_requirement", "knowledge_process",
        "knowledge_process_step", "knowledge_timing_rule", "knowledge_contact",
    ),
}


def mode_certification(mode: SourceMode) -> str:
    return "beta" if mode is SourceMode.PRETERM_KNOWLEDGE else "not_certified"
