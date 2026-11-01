"""
Feedback on Spark answers.

The platform records what was served and what it cost. This records whether
the answer was *right* -- the one signal that turns a chat log into a
training set, and the only way to prove that a new model is better than the
one it replaces.

Two rules keep the signal honest:

* a student may only rate an answer in a conversation they own;
* one verdict per student per answer, and the last one wins. A student who
  changes their mind should not have to think about which button to press.
"""

import logging
from typing import Any, Dict, Optional

from django.db import IntegrityError, transaction
from django.utils import timezone

logger = logging.getLogger(__name__)


class FeedbackError(Exception):
    """The feedback could not be recorded."""


class FeedbackService:
    """Records and reads verdicts on Spark answers."""

    MAX_COMMENT = 2000

    @classmethod
    def valid_reasons(cls):
        from pwanimate.models import PwanimateMessageFeedback

        return {choice for choice, _ in PwanimateMessageFeedback.REASON_CHOICES}

    @staticmethod
    def record(
        user,
        message_id,
        helpful: bool,
        reason: str = "",
        comment: str = "",
    ) -> Dict[str, Any]:
        """
        Record (or replace) the student's verdict on one answer.

        Raises :class:`FeedbackError` when the message does not exist or
        does not belong to the student.
        """
        from pwanimate.models import PwanimateMessage

        if user is None or not getattr(user, "is_authenticated", False):
            raise FeedbackError("You must be signed in to rate an answer.")

        message = (
            PwanimateMessage.objects.select_related("conversation")
            .filter(id=message_id)
            .first()
        )
        if message is None:
            raise FeedbackError("That answer no longer exists.")
        if message.role != "assistant":
            raise FeedbackError("Only Spark answers can be rated.")
        if message.conversation.user_id != user.id:
            raise FeedbackError("That answer belongs to someone else.")

        reason = str(reason or "").strip()
        if reason and reason not in FeedbackService.valid_reasons():
            raise FeedbackError("That is not a known reason.")

        comment = str(comment or "").strip()[: FeedbackService.MAX_COMMENT]

        with transaction.atomic():
            from pwanimate.models import PwanimateMessageFeedback

            feedback, created = PwanimateMessageFeedback.objects.update_or_create(
                message=message,
                user=user,
                defaults={
                    "helpful": bool(helpful),
                    "reason": reason,
                    "comment": comment,
                },
            )

        logger.info(
            "[FEEDBACK] message=%s user=%s helpful=%s reason=%s%s",
            message_id,
            getattr(user, "id", None),
            bool(helpful),
            reason or "-",
            " (updated)" if not created else "",
        )
        return {
            "id": str(feedback.id),
            "message_id": str(feedback.message_id),
            "helpful": feedback.helpful,
            "reason": feedback.reason,
            "comment": feedback.comment,
            "updated_at": feedback.updated_at.isoformat(),
        }

    @staticmethod
    def get_for_message(user, message_id) -> Optional[Dict[str, Any]]:
        """The student's own verdict on one answer, if they gave one."""
        from pwanimate.models import PwanimateMessageFeedback

        feedback = (
            PwanimateMessageFeedback.objects.filter(
                message_id=message_id, user=user
            )
            .first()
        )
        if feedback is None:
            return None
        return {
            "id": str(feedback.id),
            "message_id": str(feedback.message_id),
            "helpful": feedback.helpful,
            "reason": feedback.reason,
            "comment": feedback.comment,
            "updated_at": feedback.updated_at.isoformat(),
        }

    @staticmethod
    def summary(since=None) -> Dict[str, Any]:
        """
        The aggregate picture -- the first thing to look at when judging
        whether a model change helped.
        """
        from django.db.models import Count, Q

        from pwanimate.models import PwanimateMessage, PwanimateMessageFeedback

        queryset = PwanimateMessageFeedback.objects.all()
        if since is not None:
            queryset = queryset.filter(created_at__gte=since)

        total = queryset.count()
        helpful = queryset.filter(helpful=True).count()

        by_reason = {}
        for row in queryset.exclude(reason="").values("reason").annotate(count=Count("id")):
            by_reason[row["reason"]] = row["count"]

        # Which models produced the answers students liked least. This is
        # the query that decides whether a fine-tune is worth shipping.
        #
        # The rate is computed from counts rather than AVG(helpful):
        # Postgres has no avg(boolean), and a portable count is clearer
        # anyway.
        by_model = {}
        rows = (
            queryset.values("message__provider", "message__model_id", "message__task")
            .annotate(
                count=Count("id"),
                helpful_count=Count("id", filter=Q(helpful=True)),
            )
            .order_by("-count")
        )
        for row in rows:
            key = f"{row['message__provider']}:{row['message__model_id']}" or "unknown"
            count = row["count"] or 0
            by_model[key] = {
                "provider": row["message__provider"] or "",
                "model_id": row["message__model_id"] or "",
                "task": row["message__task"] or "",
                "count": count,
                "helpful_rate": round((row["helpful_count"] or 0) / count, 3) if count else None,
            }

        return {
            "total": total,
            "helpful": helpful,
            "helpful_rate": round(helpful / total, 3) if total else None,
            "by_reason": by_reason,
            "by_model": list(by_model.values()),
            "generated_at": timezone.now().isoformat(),
        }
