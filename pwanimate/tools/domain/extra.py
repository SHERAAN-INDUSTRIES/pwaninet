"""Small first-party tools for Pwanimate's clock, content search and reminders."""

from datetime import datetime
from zoneinfo import ZoneInfo

from django.utils import timezone

from pwanimate.tools.base import BaseDomainTool, ToolResult
from pwanimate.tools.exceptions import ToolPermissionError, ToolValidationError


class LocalTimeTool(BaseDomainTool):
    name = "local_time"
    description = "Report the current time in the user's browser timezone."
    parameters_schema = {"type": "object", "properties": {
        "local_time": {"type": "string"}, "timezone_name": {"type": "string"},
    }, "required": []}

    def execute(self, user, local_time="", timezone_name="UTC", **kwargs):
        try:
            if timezone_name in {"", "UTC", "Etc/UTC"}:
                timezone_name = "Africa/Nairobi"
            zone = ZoneInfo(timezone_name or "UTC")
        except Exception:
            zone = ZoneInfo("Africa/Nairobi")
            timezone_name = "Africa/Nairobi"
        try:
            now = datetime.fromisoformat(local_time.replace("Z", "+00:00")).astimezone(zone) if local_time else timezone.now().astimezone(zone)
        except (ValueError, TypeError):
            now = timezone.now().astimezone(zone)
        return ToolResult.ok({"local_time": now.isoformat(), "timezone": zone.key})


class ContentSearchTool(BaseDomainTool):
    name = "content_search"
    description = "Search accessible vasto posts or documents by text."
    parameters_schema = {"type": "object", "properties": {
        "query": {"type": "string"}, "content_type": {"type": "string"},
        "limit": {"type": "integer"},
        "document_ids": {"type": "array", "items": {"type": "integer"}},
    }, "required": ["query", "content_type"]}

    def execute(self, user, query, content_type, limit=5, document_ids=None, **kwargs):
        from pwanimate.retrieval.adapters.pwaninet_search import pwaninetSearchAdapter
        from pwanimate.retrieval.types import RetrievalRequest
        query = str(query).strip()[:300]
        if content_type not in {"post", "document"}:
            raise ToolValidationError("Search type must be post or document.", tool_name=self.name)
        if not query:
            raise ToolValidationError("Please provide search terms.", tool_name=self.name)
        filters = {}
        if content_type == "document" and document_ids:
            # Optional authorized-document scoping (e.g. the student's selected
            # context document). The search adapter applies its own access
            # rules, so this can only narrow, never widen, visibility.
            try:
                scoped = [int(value) for value in document_ids][:10]
            except (TypeError, ValueError):
                scoped = []
            if scoped:
                filters["document_ids"] = scoped
        request = RetrievalRequest(query=query, user=user, limit=min(max(int(limit), 1), 10), filters=filters)
        adapter = pwaninetSearchAdapter()
        results = adapter.search_posts(request) if content_type == "post" else adapter.search_documents_lexical(request)
        return ToolResult.ok([{
            "title": r.title, "snippet": (r.snippet or "")[:1200], "url": r.url,
            "citation": r.citation, "score": r.score,
        } for r in results], count=len(results), content_type=content_type)


class SendNotificationTool(BaseDomainTool):
    name = "send_notification"
    description = "Create an in-app reminder notification for the authenticated user only."
    parameters_schema = {"type": "object", "properties": {
        "summary": {"type": "string"},
    }, "required": ["summary"]}

    def execute(self, user, summary, **kwargs):
        if not user or not getattr(user, "is_authenticated", False):
            raise ToolPermissionError("Sign in to create a reminder.", tool_name=self.name)
        summary = " ".join(str(summary).split())[:1000]
        if not summary:
            raise ToolValidationError("Reminder text is required.", tool_name=self.name)
        from notifications.models import NotificationObject
        notification = NotificationObject.objects.create(
            recipient=user, notification_type="AI", category="AI", priority="NORMAL",
            title="Pwanimate reminder", summary=summary,
            context_type="PWANIMATE", context_id="reminder", status="DELIVERED",
            delivery_policy="IMMEDIATE", metadata={"source": "pwanimate"},
        )
        from notifications.services.notification_service import invalidate_unread_count_cache, broadcast_unread_count
        invalidate_unread_count_cache(user.id)
        unread_count = None
        try:
            from notifications.queries.notification_queries import get_unread_count
            unread_count = get_unread_count(user)
            from channels.layers import get_channel_layer
            from asgiref.sync import async_to_sync
            channel_layer = get_channel_layer()
            if channel_layer:
                async_to_sync(channel_layer.group_send)(f"notifications_{user.id}", {
                    "type": "notification",
                    "notification": {
                        "notification_id": str(notification.notification_id),
                        "type": "AI", "category": "AI", "priority": "NORMAL",
                        "title": notification.title, "summary": notification.summary,
                        "context_type": "PWANIMATE", "context_id": "reminder",
                        "created_at": notification.created_at.isoformat(),
                        "unread_count": unread_count, "metadata": notification.metadata,
                    },
                    "unread_count": unread_count,
                })
        except Exception:
            broadcast_unread_count(user.id)
        return ToolResult.ok({"title": notification.title, "summary": notification.summary,
                              "notification_id": str(notification.notification_id)})
