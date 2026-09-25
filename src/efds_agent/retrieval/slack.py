from efds_agent.citations.models import Citation
from efds_agent.retrieval.base import RetrievalRequest, RetrievalResult
from efds_agent.retrieval.helpers import evidence, lexical_relevance, term_filter, text
from efds_agent.retrieval.supabase import DataAccessError, SupabaseRestClient
from efds_agent.security.authorization import AuthContext
from efds_agent.security.scopes import can_read_source


class SlackAdapter:
    source = "slack"

    def __init__(self, client: SupabaseRestClient) -> None:
        self.client = client

    async def search(self, request: RetrievalRequest, auth: AuthContext) -> RetrievalResult:
        if not can_read_source(auth.scope.effective_scope, self.source):
            return RetrievalResult(source=self.source, queried=False, note="Slack is admin-only under current RLS")
        params = [("is_deleted", "eq.false"), ("limit", str(request.max_results))]
        if request.channel:
            params.append(("channel_id", f"eq.{request.channel}"))
        if request.author:
            params.append(("user_slack_id", f"eq.{request.author}"))
        if request.date_from:
            params.append(("source_posted_at", f"gte.{request.date_from}"))
        if request.date_to:
            params.append(("source_posted_at", f"lt.{request.date_to}"))
        query = term_filter(request.terms, ("message_text",))
        if query:
            params.append(("or", query))
        try:
            rows = await self.client.select(
                "slack_messages",
                "id,channel_id,slack_ts,thread_ts,parent_message_id,message_text,source_posted_at,permalink,content_hash,user_slack_id",
                params,
            )
        except DataAccessError:
            rows = []
        channel_ids = list(dict.fromkeys(text(row.get("channel_id")) for row in rows if row.get("channel_id")))
        names: dict[str, str] = {}
        if channel_ids:
            try:
                channel_rows = await self.client.select(
                    "slack_channels", "id,name", [("id", "in.(" + ",".join(channel_ids) + ")")]
                )
                names = {text(row.get("id")): text(row.get("name"), "unknown") for row in channel_rows}
            except DataAccessError:
                names = {}
        found = []
        seen_threads: set[str] = set()
        for row in rows:
            channel = names.get(text(row.get("channel_id")), "unknown-channel")
            title = "#" + channel
            thread_key = text(row.get("thread_ts")) or text(row.get("slack_ts"))
            thread_identity = f"{text(row.get('channel_id'))}:{thread_key}"
            if thread_identity in seen_threads:
                continue
            seen_threads.add(thread_identity)
            thread_rows = [row]
            if thread_key:
                thread_query = f"(slack_ts.eq.{thread_key},thread_ts.eq.{thread_key})"
                try:
                    thread_rows = await self.client.select(
                        "slack_messages",
                        "id,channel_id,slack_ts,thread_ts,message_text,source_posted_at,permalink,user_slack_id,content_hash",
                        [
                            ("is_deleted", "eq.false"),
                            ("channel_id", f"eq.{row.get('channel_id')}"),
                            ("or", thread_query),
                            ("order", "source_posted_at.asc"),
                            ("limit", "12"),
                        ],
                    )
                except DataAccessError:
                    thread_rows = [row]
            lines = [
                f"{text(message.get('source_posted_at'))}: {text(message.get('message_text'))}"
                for message in thread_rows
                if text(message.get("message_text"))
            ]
            body = "\n".join(lines)
            citation = Citation(
                id="pending",
                source_type="slack_message",
                source_id=text(row.get("id") or row.get("slack_ts")),
                title=title + " thread",
                url=row.get("permalink"),
                channel=channel,
                timestamp=row.get("source_posted_at"),
                content_hash=row.get("content_hash"),
                authority="canonical_slack",
            )
            item = evidence(
                citation,
                body,
                lexical_relevance((body,), request.terms, 0.55),
                {
                    "slack_ts": row.get("slack_ts"),
                    "thread_ts": row.get("thread_ts"),
                    "canonical_message_ids": [message.get("id") for message in thread_rows],
                    "author": row.get("user_slack_id"),
                },
            )
            if item:
                found.append(item)
        return RetrievalResult(
            source=self.source,
            evidence=sorted(found, key=lambda item: item.relevance, reverse=True)[: request.max_results],
        )
