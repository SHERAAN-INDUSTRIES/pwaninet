"""
Actions Spark can take on the student's behalf.

Both tools here **propose**; neither performs. The tool creates a pending
action carrying the exact thing that would happen, and the student confirms
it in the app. See :mod:`pwanimate.services.actions` for why.

The two actions are deliberately the lowest-risk ones that still turn Spark
into an agent: message one person, follow one person. Both are reversible,
both are individually consented to, and both exercise the whole write
pipeline -- so the next one (post an announcement, book a tutor slot, pay
with M-Pesa) inherits a safety model that has already been used.
"""

from typing import Any, Dict, Optional

from pwanimate.tools.base import BaseDomainTool, ToolResult
from pwanimate.tools.exceptions import ToolPermissionError, ToolValidationError

MAX_MESSAGE_LENGTH = 1000


class _ActionTool(BaseDomainTool):
    """Shared plumbing for the write-action tools."""

    def _require_user(self, user) -> None:
        if not user or not getattr(user, "is_authenticated", False):
            raise ToolPermissionError(
                "Sign in to let Spark act for you.", tool_name=self.name
            )

    def _resolve_target(self, user, username: Optional[str], user_id: Optional[int]):
        """Find the person the action is aimed at, respecting blocks."""
        from django.contrib.auth import get_user_model
        from users.models import Block

        if not username and not user_id:
            raise ToolValidationError(
                "Say who the action is for: 'username' or 'user_id'.",
                tool_name=self.name,
            )

        queryset = get_user_model().objects.filter(is_active=True)
        if username:
            target = queryset.filter(username__iexact=str(username).strip()).first()
        else:
            target = queryset.filter(id=user_id).first()

        if target is None:
            raise ToolValidationError(
                f"No active user called '@{username}'." if username else
                f"No active user with id {user_id}.",
                tool_name=self.name,
            )
        if target.id == user.id:
            raise ToolValidationError(
                "You cannot do that to yourself.", tool_name=self.name
            )
        if Block.objects.filter(
            blocker=user, blocked=target
        ).exists() or Block.objects.filter(
            blocker=target, blocked=user
        ).exists():
            raise ToolPermissionError(
                "That connection is unavailable.", tool_name=self.name
            )
        return target


class SendMessageTool(_ActionTool):
    """
    Draft a message to another student and ask them to approve it.

    Spark never sends on its own. It prepares the message, shows the
    student exactly what would be sent, and sends it only when they say so.
    """

    name = "send_message"
    description = (
        "Propose a direct message to another vasto user for the student to "
        "review and send. Spark never sends without approval."
    )
    parameters_schema = {
        "type": "object",
        "properties": {
            "username": {
                "type": "string",
                "description": "Username of the person to message (without the @).",
            },
            "message": {
                "type": "string",
                "description": "The message text to send.",
            },
        },
        "required": ["username", "message"],
    }

    def execute(
        self,
        user: Any,
        username: Optional[str] = None,
        message: Optional[str] = None,
        conversation=None,
        **kwargs,
    ) -> ToolResult:
        self._require_user(user)
        target = self._resolve_target(user, username, kwargs.get("user_id"))

        body = " ".join(str(message or "").split())
        if not body:
            raise ToolValidationError("The message is empty.", tool_name=self.name)
        if len(body) > MAX_MESSAGE_LENGTH:
            raise ToolValidationError(
                f"The message is too long (limit {MAX_MESSAGE_LENGTH} characters).",
                tool_name=self.name,
            )

        from pwanimate.services.actions import ActionError, ActionService

        summary = f'Send to @{target.username}: "{body}"'
        try:
            action = ActionService.propose(
                user=user,
                action_type="send_message",
                params={"username": target.username, "message": body},
                summary=summary,
                conversation=conversation,
            )
        except ActionError as exc:
            raise ToolPermissionError(str(exc), tool_name=self.name)

        return ToolResult.ok(
            {
                "action": ActionService.serialize(action),
                "recipient": target.username,
                "message": body,
                "needs_approval": True,
            },
            action_id=str(action.id),
        )


class FollowUserTool(_ActionTool):
    """
    Propose following someone so the student can approve it.

    A follow creates a notification on their side, so it is not a thing
    Spark should do quietly.
    """

    name = "follow_user"
    description = (
        "Propose following another vasto user for the student to review and "
        "approve. Spark never follows without approval."
    )
    parameters_schema = {
        "type": "object",
        "properties": {
            "username": {
                "type": "string",
                "description": "Username of the person to follow (without the @).",
            },
        },
        "required": ["username"],
    }

    def execute(
        self,
        user: Any,
        username: Optional[str] = None,
        conversation=None,
        **kwargs,
    ) -> ToolResult:
        self._require_user(user)
        target = self._resolve_target(user, username, kwargs.get("user_id"))

        from pwanimate.services.actions import ActionError, ActionService

        summary = f"Follow @{target.username}"
        try:
            action = ActionService.propose(
                user=user,
                action_type="follow_user",
                params={"username": target.username},
                summary=summary,
                conversation=conversation,
            )
        except ActionError as exc:
            raise ToolPermissionError(str(exc), tool_name=self.name)

        return ToolResult.ok(
            {
                "action": ActionService.serialize(action),
                "recipient": target.username,
                "needs_approval": True,
            },
            action_id=str(action.id),
        )
