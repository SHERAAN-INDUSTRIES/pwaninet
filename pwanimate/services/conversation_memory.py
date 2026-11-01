"""Semantic recall over a student's own persisted conversation messages."""

import logging
import re
from typing import Any, Dict, List, Optional

from django.db.models import Q
from pgvector.django import CosineDistance

from pwanimate.ai.embeddings.factory import get_embedding_provider
from pwanimate.models import PwanimateMessage, PwanimateMessageEmbedding

logger = logging.getLogger(__name__)

_STOP_WORDS = {
    "about", "after", "again", "could", "from", "have", "what", "when",
    "where", "which", "with", "would", "your", "this", "that", "last",
    "conversation", "discuss", "discussed",
}


class ConversationMemoryService:
    """Build bounded, owner-scoped recall context for an active turn."""

    MAX_QUERY_LENGTH = 2000
    DEFAULT_LIMIT = 6
    MAX_FALLBACK_CANDIDATES = 300

    @classmethod
    def search(
        cls,
        user: Any,
        query: str,
        *,
        exclude_conversation_id: Optional[Any] = None,
        limit: int = DEFAULT_LIMIT,
    ) -> List[Dict[str, Any]]:
        if not user or not getattr(user, "is_authenticated", False):
            return []
        query = str(query or "").strip()[: cls.MAX_QUERY_LENGTH]
        if not query:
            return []

        limit = max(1, min(int(limit or cls.DEFAULT_LIMIT), 12))
        qs = PwanimateMessageEmbedding.objects.filter(
            user=user,
            status="completed",
            embedding__isnull=False,
        ).select_related("message", "message__conversation")
        if exclude_conversation_id:
            qs = qs.exclude(message__conversation_id=exclude_conversation_id)

        try:
            provider = get_embedding_provider()
            model_name = provider.get_model_name()
            vector = provider.embed_query(query)
            semantic_qs = qs.filter(embedding_model=model_name).annotate(
                distance=CosineDistance("embedding", vector)
            ).filter(distance__isnull=False).order_by("distance")[:limit]
            results = [
                cls._serialize(
                    record.message,
                    score=max(0.0, min(1.0, 1.0 - float(record.distance))),
                    match_type="semantic",
                )
                for record in semantic_qs
            ]
            if results:
                return results
        except Exception:
            logger.warning("Conversation semantic recall unavailable; using lexical fallback", exc_info=True)

        return cls._lexical_fallback(
            user,
            query,
            exclude_conversation_id=exclude_conversation_id,
            limit=limit,
        )

    @classmethod
    def _lexical_fallback(
        cls,
        user: Any,
        query: str,
        *,
        exclude_conversation_id: Optional[Any],
        limit: int,
    ) -> List[Dict[str, Any]]:
        terms = [
            term.lower()
            for term in re.findall(r"[a-zA-Z0-9]{4,}", query)
            if term.lower() not in _STOP_WORDS
        ]
        if not terms:
            return []
        filters = Q()
        for term in terms[:8]:
            filters |= Q(content__icontains=term)
        messages_qs = PwanimateMessage.objects.filter(
            conversation__user=user,
            role__in=("user", "assistant"),
        ).filter(filters).select_related("conversation").order_by("-created_at")
        if exclude_conversation_id:
            messages_qs = messages_qs.exclude(conversation_id=exclude_conversation_id)
        messages = messages_qs[: cls.MAX_FALLBACK_CANDIDATES]

        scored = []
        for message in messages:
            text = (message.content or "").lower()
            hits = sum(1 for term in terms if term in text)
            if hits:
                scored.append((hits, message))
        scored.sort(key=lambda item: (item[0], item[1].created_at), reverse=True)
        return [
            cls._serialize(message, score=min(0.99, hits / max(1, len(terms))), match_type="lexical")
            for hits, message in scored[:limit]
        ]

    @staticmethod
    def _serialize(message: PwanimateMessage, *, score: float, match_type: str) -> Dict[str, Any]:
        return {
            "message_id": message.id,
            "conversation_id": str(message.conversation_id),
            "conversation_title": message.conversation.title or "Conversation",
            "role": message.role,
            "content": (message.content or "").strip()[:1200],
            "created_at": message.created_at.isoformat(),
            "score": round(float(score), 4),
            "match_type": match_type,
        }
