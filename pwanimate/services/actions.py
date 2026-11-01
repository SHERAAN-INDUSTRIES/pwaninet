"""
Actions Spark proposes and the student approves.

This is the difference between an assistant and an agent. A read-only tool
answers a question. A write action changes the world: a message reaches a
classmate, a follow reaches a lecturer, a post reaches a campus. An agent
that can do that without asking will eventually embarrass somebody, and a
student who gets burned once does not come back.

So the model *proposes* and the student *confirms*. Nothing here sends
anything.

The safety rules, and where each one lives:

1. **Confirmation** — :meth:`ActionService.propose` only creates a pending
   action. :meth:`ActionService.confirm` is the only thing that performs it.
2. **Idempotency** — every action carries a key derived from its content, so
   a retry returns the same pending action instead of sending twice.
3. **A real dry run** — the proposal carries the exact message that would be
   sent, so the student approves the real thing and not a summary of it.
4. **Rate limits** — proposals and executions are both capped per student
   per hour. A model that loops cannot spam a campus.
5. **Undo** — a confirmed action can be undone inside a short window: a sent
   message is soft-deleted, a follow is released.

The two actions here are deliberately the lowest-risk ones that still make
Spark an agent rather than a chatbot: messaging one person, and following one
person. Both are reversible, both are individually consented to, and both
exercise the entire write pipeline.
"""

import hashlib
import json
import logging
from datetime import timedelta
from typing import Any, Dict, Optional

from django.db import transaction
from django.utils import timezone

logger = logging.getLogger(__name__)

# How long a proposal stays confirmable.
PENDING_TTL_MINUTES = 15
# How long a confirmed action can be undone.
UNDO_WINDOW_MINUTES = 30
# Ceilings per student per hour. Proposals are capped separately from
# executions: a model that spams proposals is annoying, a model that spams
# executions is a problem.
MAX_PROPOSALS_PER_HOUR = 20
MAX_EXECUTIONS_PER_HOUR = 10
# Length caps on what Spark may put in the world.
MAX_MESSAGE_LENGTH = 1000


class ActionError(Exception):
    """The action could not be proposed or performed."""


class ActionService:
    """Proposes, confirms, cancels and undoes student-approved actions."""

    # ------------------------------------------------------------- proposing
    @staticmethod
    def _idempotency_key(user, action_type: str, params: Dict[str, Any], conversation_id=None) -> str:
        canonical = json.dumps(
            {"action": action_type, "params": params, "conversation": str(conversation_id or "")},
            sort_keys=True,
            default=str,
        )
        digest = hashlib.sha256(f"{user.id}:{canonical}".encode("utf-8")).hexdigest()
        return digest[:64]

    @staticmethod
    def _rate_limit(user, action_type: str, limit: int, window_minutes: int, what: str) -> None:
        from pwanimate.models import PendingAction

        since = timezone.now() - timedelta(minutes=window_minutes)
        recent = PendingAction.objects.filter(
            user=user,
            created_at__gte=since,
        )
        if action_type:
            recent = recent.filter(action_type=action_type)
        count = recent.count()
        if count >= limit:
            raise ActionError(
                f"You have reached the limit of {limit} {what} in the last hour. "
                "Try again later."
            )

    @classmethod
    def propose(
        cls,
        user,
        action_type: str,
        params: Dict[str, Any],
        summary: str = "",
        conversation=None,
    ):
        """
        Create a pending action for the student to approve.

        This never performs the action. It only prepares it.
        """
        from pwanimate.models import PendingAction

        if user is None or not getattr(user, "is_authenticated", False):
            raise ActionError("Sign in to let Spark act for you.")

        cls._rate_limit(
            user, action_type, MAX_PROPOSALS_PER_HOUR, 60, "action proposals"
        )

        key = cls._idempotency_key(user, action_type, params, getattr(conversation, "id", None))

        # A retry of the same action returns the same proposal rather than
        # a second one.
        existing = PendingAction.objects.filter(idempotency_key=key).first()
        if existing is not None:
            if existing.status == PendingAction.STATUS_PENDING and existing.expires_at > timezone.now():
                return existing
            if existing.status in (
                PendingAction.STATUS_CONFIRMED,
                PendingAction.STATUS_CANCELLED,
                PendingAction.STATUS_UNDONE,
            ):
                # Already handled. The student asked for this once; doing it
                # twice because the model looped is not acceptable.
                return existing

        action = PendingAction.objects.create(
            user=user,
            conversation=conversation,
            action_type=action_type,
            params=params,
            summary=summary,
            idempotency_key=key,
            expires_at=timezone.now() + timedelta(minutes=PENDING_TTL_MINUTES),
        )
        logger.info(
            "[ACTIONS] proposed %s for user=%s action=%s",
            action_type, user.id, action.id,
        )
        return action

    # ------------------------------------------------------------ confirming
    @classmethod
    def confirm(cls, user, action_id):
        """
        Execute a pending action the student approved.

        Idempotent: confirming an already-confirmed action returns the same
        result rather than acting twice.
        """
        from pwanimate.models import PendingAction

        action = PendingAction.objects.filter(id=action_id).first()
        if action is None:
            raise ActionError("That action is no longer available.")
        if action.user_id != getattr(user, "id", None):
            raise ActionError("That action belongs to someone else.")
        if action.status == PendingAction.STATUS_CONFIRMED:
            return action
        if action.status != PendingAction.STATUS_PENDING:
            raise ActionError(f"That action is {action.status} and cannot be confirmed.")
        if action.expires_at <= timezone.now():
            action.status = PendingAction.STATUS_EXPIRED
            action.save(update_fields=["status"])
            raise ActionError("That proposal expired. Ask Spark again if you still want it.")

        cls._rate_limit(user, action.action_type, MAX_EXECUTIONS_PER_HOUR, 60, "actions")

        try:
            if action.action_type == PendingAction.ACTION_SEND_MESSAGE:
                result = cls._execute_send_message(action)
            elif action.action_type == PendingAction.ACTION_FOLLOW_USER:
                result = cls._execute_follow_user(action)
            else:
                raise ActionError("That action is not supported.")
        except ActionError as exc:
            action.status = PendingAction.STATUS_FAILED
            action.error = str(exc)[:2000]
            action.save(update_fields=["status", "error"])
            raise
        except Exception as exc:
            logger.exception("[ACTIONS] action %s failed", action.id)
            action.status = PendingAction.STATUS_FAILED
            action.error = f"Action failed: {exc}"[:2000]
            action.save(update_fields=["status", "error"])
            raise ActionError("That action could not be completed.")

        action.status = PendingAction.STATUS_CONFIRMED
        action.result = result or {}
        action.confirmed_at = timezone.now()
        action.save(update_fields=["status", "result", "confirmed_at"])
        logger.info(
            "[ACTIONS] confirmed %s for user=%s action=%s",
            action.action_type, user.id, action.id,
        )
        return action

    @classmethod
    def cancel(cls, user, action_id):
        """Decline a proposal."""
        from pwanimate.models import PendingAction

        action = PendingAction.objects.filter(id=action_id).first()
        if action is None:
            raise ActionError("That action is no longer available.")
        if action.user_id != getattr(user, "id", None):
            raise ActionError("That action belongs to someone else.")
        if action.status != PendingAction.STATUS_PENDING:
            raise ActionError(f"That action is already {action.status}.")
        action.status = PendingAction.STATUS_CANCELLED
        action.save(update_fields=["status"])
        return action

    @classmethod
    def undo(cls, user, action_id):
        """
        Reverse a confirmed action inside the undo window.

        A sent message is soft-deleted; a follow is released. A message that
        has been read cannot be un-sent, and pretending otherwise would be
        worse than saying so.
        """
        from pwanimate.models import PendingAction

        action = PendingAction.objects.filter(id=action_id).first()
        if action is None:
            raise ActionError("That action is no longer available.")
        if action.user_id != getattr(user, "id", None):
            raise ActionError("That action belongs to someone else.")
        if action.status != PendingAction.STATUS_CONFIRMED:
            raise ActionError("Only a completed action can be undone.")

        deadline = (action.confirmed_at or timezone.now()) + timedelta(minutes=UNDO_WINDOW_MINUTES)
        if timezone.now() > deadline:
            raise ActionError("That was too long ago to undo.")

        if action.action_type == PendingAction.ACTION_SEND_MESSAGE:
            cls._undo_send_message(action)
        elif action.action_type == PendingAction.ACTION_FOLLOW_USER:
            cls._undo_follow_user(action)

        action.status = PendingAction.STATUS_UNDONE
        action.undone_at = timezone.now()
        action.save(update_fields=["status", "undone_at"])
        return action

    # ---------------------------------------------------------- the actions
    @staticmethod
    def _execute_send_message(action) -> Dict[str, Any]:
        from django.contrib.auth import get_user_model
        from messaging.models import Conversation, ConversationMember, Message
        from messaging.services.chat_notification_service import ChatNotificationService
        from users.models import Block

        params = action.params or {}
        username = str(params.get("username") or "").strip()
        body = str(params.get("message") or "").strip()
        if not body:
            raise ActionError("The message is empty.")
        if len(body) > MAX_MESSAGE_LENGTH:
            raise ActionError(
                f"The message is too long (limit {MAX_MESSAGE_LENGTH} characters)."
            )

        target = (
            get_user_model()
            .objects.filter(username__iexact=username, is_active=True)
            .first()
        )
        if target is None:
            raise ActionError(f"@{username} does not exist.")
        if target.id == action.user_id:
            raise ActionError("You cannot message yourself.")
        if Block.objects.filter(
            blocker=action.user, blocked=target
        ).exists() or Block.objects.filter(
            blocker=target, blocked=action.user
        ).exists():
            raise ActionError("You cannot message this person.")

        with transaction.atomic():
            conversation = Conversation.get_direct_conversation_between(action.user, target)
            if conversation is None:
                conversation = Conversation.objects.create(type=Conversation.DIRECT)
                ConversationMember.objects.get_or_create(
                    conversation=conversation, user=action.user
                )
                ConversationMember.objects.get_or_create(
                    conversation=conversation, user=target
                )
            message = Message.objects.create(
                conversation=conversation,
                sender=action.user,
                content=body,
                status="sent",
            )
            conversation.save(update_fields=["updated_at"])

        # Realtime broadcast, mirroring the normal send path.
        try:
            from asgiref.sync import async_to_sync
            from channels.layers import get_channel_layer

            channel_layer = get_channel_layer()
            if channel_layer:
                async_to_sync(channel_layer.group_send)(
                    f"chat_{conversation.id}",
                    {"type": "chat_message", "message": {"id": str(message.id)}},
                )
        except Exception:
            logger.warning("[ACTIONS] realtime broadcast failed for action %s", action.id)

        # Notification side effects, exactly as the normal send path does.
        try:
            ChatNotificationService.notify_new_message(message)
        except Exception:
            logger.warning("[ACTIONS] chat notification failed for action %s", action.id)

        return {
            "message_id": str(message.id),
            "conversation_id": str(conversation.id),
            "recipient": target.username,
            "message": body,
        }

    @staticmethod
    def _undo_send_message(action) -> None:
        from messaging.models import Message

        message_id = (action.result or {}).get("message_id")
        if not message_id:
            return
        message = Message.objects.filter(id=message_id, sender=action.user).first()
        if message is None:
            return
        message.is_deleted = True
        message.save(update_fields=["is_deleted"])

    @staticmethod
    def _execute_follow_user(action) -> Dict[str, Any]:
        from django.contrib.auth import get_user_model
        from notifications.services.notification_service import invalidate_unread_count_cache
        from users.models import Block, Follow

        params = action.params or {}
        username = str(params.get("username") or "").strip()
        target = (
            get_user_model()
            .objects.filter(username__iexact=username, is_active=True)
            .first()
        )
        if target is None:
            raise ActionError(f"@{username} does not exist.")
        if target.id == action.user_id:
            raise ActionError("You cannot follow yourself.")
        if Block.objects.filter(
            blocker=action.user, blocked=target
        ).exists() or Block.objects.filter(
            blocker=target, blocked=action.user
        ).exists():
            raise ActionError("You cannot follow this person.")

        with transaction.atomic():
            follow, created = Follow.objects.get_or_create(
                follower=action.user, followed=target
            )

        try:
            invalidate_unread_count_cache(target.id)
        except Exception:
            pass
        try:
            from users.services.friend_suggestion_service import (
                invalidate_friend_suggestions_cache,
            )

            invalidate_friend_suggestions_cache(action.user.id)
        except Exception:
            pass

        return {
            "follow_id": str(follow.id),
            "created": created,
            "followed": target.username,
        }

    @staticmethod
    def _undo_follow_user(action) -> None:
        from users.models import Follow

        target_username = (action.result or {}).get("followed")
        if not target_username:
            return
        Follow.objects.filter(
            follower=action.user, followed__username=target_username
        ).delete()

    # ------------------------------------------------------------- presenting
    @staticmethod
    def serialize(action) -> Dict[str, Any]:
        """The payload the client renders as a confirm/cancel card."""
        from pwanimate.models import PendingAction

        return {
            "id": str(action.id),
            "action_type": action.action_type,
            "status": action.status,
            "summary": action.summary,
            "params": action.params or {},
            "result": action.result or {},
            "error": action.error,
            "created_at": action.created_at.isoformat(),
            "expires_at": action.expires_at.isoformat(),
            "confirmed_at": action.confirmed_at.isoformat() if action.confirmed_at else None,
            "can_confirm": action.status == PendingAction.STATUS_PENDING
            and action.expires_at > timezone.now(),
            "can_undo": action.status == PendingAction.STATUS_CONFIRMED
            and (action.confirmed_at or timezone.now())
            + timedelta(minutes=UNDO_WINDOW_MINUTES) > timezone.now(),
        }
