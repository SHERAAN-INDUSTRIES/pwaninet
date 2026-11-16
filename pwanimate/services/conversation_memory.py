"""Compatibility entry point for relevant owner-approved facts, never transcripts."""

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
        if not str(query or '').strip():
            return []
        from pwanimate.services.personal_memory import PersonalMemoryService
        return PersonalMemoryService.recall(user, query, limit=limit)

    @classmethod
    def _lexical_fallback(
        cls,
        user: Any,
        query: str,
        *,
        exclude_conversation_id: Optional[Any],
        limit: int,
    ) -> List[Dict[str, Any]]:
        # Kept as a compatibility hook; it must never search raw conversations.
        return cls.search(user, query, limit=limit)

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
