"""
Conversation Service for Pwanimate.

Manages persistent multi-turn conversations, ownership enforcement,
bounded history loading with token limits, and turn persistence.
"""

from typing import List, Optional
import logging
import uuid

from django.db import transaction

from pwanimate.ai.gateway.types import ChatMessage, AttachmentData
from pwanimate.context.types import estimate_tokens
from pwanimate.models import PwanimateConversation, PwanimateMessage

logger = logging.getLogger(__name__)


import re

GREETINGS = {
    "hi", "hello", "hey", "hey there", "hi there", "hello there",
    "good morning", "good afternoon", "good evening", "greetings",
    "yo", "sup", "howdy", "habari", "mambo", "sasa", "jambo",
    "pwanimate", "hello pwanimate", "hi pwanimate", "hey pwanimate",
}

CONVERSATIONAL_FILLER_REGEX = re.compile(
    r"^(could\s+you\s+(please\s+)?(help\s+me\s+(to\s+)?(understand|find|get)\s+|tell\s+me\s+about\s+|explain\s+)?|"
    r"can\s+you\s+(please\s+)?(help\s+me\s+(to\s+)?(understand|find|get)\s+|tell\s+me\s+about\s+|explain\s+|show\s+me\s+)?|"
    r"please\s+(help\s+me\s+(to\s+)?(understand|find|get)\s+|tell\s+me\s+about\s+|explain\s+|show\s+me\s+)?|"
    r"i\s+(would\s+like|want)\s+to\s+(know\s+about|find|learn\s+about|understand)\s+|"
    r"i\s+need\s+help\s+(with|finding|understanding)\s+)\s*",
    re.IGNORECASE,
)


def derive_title(content: str, max_words: int = 8, max_length: int = 60, fallback: str = "Conversation") -> str:
    """
    Derive a concise, deterministic 3-8 word conversation title from user query.
    Returns empty string for pure greetings/filler to defer naming to turn 2.
    """
    if not content:
        return fallback

    # Strip code blocks, inline code, URLs, HTML tags
    cleaned = re.sub(r"```[\s\S]*?```", " ", content)
    cleaned = re.sub(r"`.*?`", " ", cleaned)
    cleaned = re.sub(r"https?://\S+|www\.\S+", " ", cleaned)
    cleaned = re.sub(r"<[^>]+>", " ", cleaned)
    cleaned = " ".join(cleaned.split())
    if not cleaned:
        return fallback

    # Check for greeting/filler (returns empty string so turn 2 names conversation)
    greeting_probe = re.sub(r"[^\w\s]", "", cleaned.lower()).strip()
    if greeting_probe in GREETINGS:
        return ""

    # Strip conversational question filler openers
    stripped = CONVERSATIONAL_FILLER_REGEX.sub("", cleaned).strip()
    if not stripped:
        stripped = cleaned

    words = stripped.split()
    if not words:
        return fallback

    selected = words[:max_words]
    raw_title = " ".join(selected)
    if len(raw_title) > max_length:
        raw_title = raw_title[:max_length - 3].rstrip() + "..."

    return raw_title[:1].upper() + raw_title[1:]


class ConversationService:
    """
    Service handling conversation lifecycle and message persistence.
    """

    @staticmethod
    def create_conversation(
        user,
        title: str = "",
        conversation_id: Optional[uuid.UUID] = None,
    ) -> PwanimateConversation:
        """
        Create a new persistent conversation for the given user.
        """
        return PwanimateConversation.objects.create(
            user=user,
            title=title.strip() if title else "",
            **({"id": conversation_id} if conversation_id else {}),
        )

    @staticmethod
    def get_owned_conversation(user, conversation_id: str | uuid.UUID) -> Optional[PwanimateConversation]:
        """
        Retrieve a conversation owned strictly by the authenticated user.
        Returns None if conversation does not exist or belongs to another user.
        """
        if not conversation_id:
            return None

        # Validate UUID format
        if isinstance(conversation_id, str):
            try:
                conversation_id = uuid.UUID(conversation_id.strip())
            except (ValueError, AttributeError):
                return None

        try:
            return PwanimateConversation.objects.get(
                id=conversation_id,
                user=user,
            )
        except PwanimateConversation.DoesNotExist:
            return None

    @staticmethod
    def load_history(
        conversation: PwanimateConversation,
        max_messages: int = 10,
        max_tokens: int = 1500,
        exclude_message_id: Optional[int] = None,
    ) -> List[ChatMessage]:
        """
        Load bounded conversation history suitable for passing to the Orchestrator.

        1. Fetches the latest max_messages ordered by -created_at using index.
        2. Reverses to chronological order.
        3. Enforces token budget: keeps the newest complete turns within budget.
        4. Converts to ChatMessage contracts.
        """
        # Fetch newest messages first with prefetched attachments
        messages_qs = conversation.messages.filter(role__in=["user", "assistant"])
        if exclude_message_id is not None:
            messages_qs = messages_qs.exclude(id=exclude_message_id)
        recent_records = list(
            messages_qs
            .prefetch_related("attachments")
            .order_by("-created_at")[:max_messages]
        )

        # Reverse back to chronological order (oldest to newest)
        chronological_records = list(reversed(recent_records))

        # Enforce token budget by pruning from oldest if necessary
        while chronological_records:
            total_tokens = sum(estimate_tokens(m.content) for m in chronological_records)
            if total_tokens <= max_tokens or len(chronological_records) <= 1:
                break
            # Drop the oldest message in the window
            chronological_records.pop(0)

        chat_messages = []
        for m in chronological_records:
            att_data_list = []
            for att in m.attachments.all():
                att_bytes = None
                if att.attachment_type == "image" and att.file:
                    try:
                        att.file.seek(0)
                        att_bytes = att.file.read()
                        att.file.seek(0)
                    except Exception:
                        pass
                att_data_list.append(
                    AttachmentData(
                        id=str(att.id),
                        name=att.file_name,
                        mime_type=att.mime_type,
                        attachment_type=att.attachment_type,
                        data_bytes=att_bytes,
                        url=att.url,
                    )
                )
            chat_messages.append(
                ChatMessage(role=m.role, content=m.content, attachments=att_data_list)
            )

        return chat_messages

    @staticmethod
    def get_retryable_user_message(
        conversation: PwanimateConversation,
        content: str,
        message_id: Optional[int] = None,
    ) -> Optional[PwanimateMessage]:
        """Find the original unanswered user turn for a retry request."""
        messages = conversation.messages.filter(role="user")
        if message_id is not None:
            return messages.filter(id=message_id).first()

        for candidate in messages.filter(content=content).order_by("-id")[:10]:
            has_later_answer = conversation.messages.filter(
                role="assistant",
                id__gt=candidate.id,
            ).exists()
            if not has_later_answer:
                return candidate
        return None

    @staticmethod
    def persist_user_message(
        conversation: PwanimateConversation,
        content: str,
    ) -> PwanimateMessage:
        """
        Persist a user turn in a short, atomic database transaction.
        Updates conversation title if currently empty and touches updated_at.
        """
        clean_content = content.strip()
        with transaction.atomic():
            msg = PwanimateMessage.objects.create(
                conversation=conversation,
                role="user",
                content=clean_content,
            )
            update_fields = ["updated_at"]
            if not conversation.title:
                new_title = derive_title(clean_content)
                if new_title:
                    conversation.title = new_title
                    update_fields.append("title")

            conversation.save(update_fields=update_fields)
            transaction.on_commit(lambda: ConversationService.schedule_embedding(msg.id))
            return msg

    @staticmethod
    def persist_assistant_message(
        conversation: PwanimateConversation,
        content: str,
        citations: Optional[list] = None,
        sources: Optional[list] = None,
        provider: str = "",
        model_id: str = "",
        task: str = "",
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        total_tokens: int = 0,
    ) -> PwanimateMessage:
        """
        Persist an assistant turn with grounding citations and source provenance.

        The serving model and the task are stored on the message as well as
        in the billing ledger: the ledger says what a turn cost, this says
        what the student was shown, and joining the two is what makes it
        possible to judge whether a model change actually helped.
        Touches conversation updated_at in a short atomic transaction.
        """
        with transaction.atomic():
            msg = PwanimateMessage.objects.create(
                conversation=conversation,
                role="assistant",
                content=content.strip(),
                citations=citations or [],
                sources=sources or [],
                provider=str(provider or "")[:32],
                model_id=str(model_id or "")[:160],
                task=str(task or "")[:64],
                prompt_tokens=int(prompt_tokens or 0),
                completion_tokens=int(completion_tokens or 0),
                total_tokens=int(total_tokens or 0),
            )
            conversation.save(update_fields=["updated_at"])
            transaction.on_commit(lambda: ConversationService.schedule_embedding(msg.id))
            return msg

    @staticmethod
    def schedule_embedding(message_id: int) -> None:
        """Queue semantic indexing without making message persistence depend on Celery."""
        try:
            from pwanimate.tasks.conversation_memory import embed_message

            delay_fn = getattr(embed_message, "delay", None)
            if callable(delay_fn):
                delay_fn(message_id)
        except Exception:
            logger.warning("Could not schedule conversation message embedding", exc_info=True)
