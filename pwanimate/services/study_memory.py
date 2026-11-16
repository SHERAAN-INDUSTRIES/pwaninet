"""Durable, source-linked study continuity. Generation never runs on the greeting request."""
import json
import logging
from django.db import transaction
from django.utils import timezone
from pwanimate.models import (PwanimateConversation, PwanimateMessage, PwanimateMessageEmbedding,
    SparkMemoryPreference, SparkConversationDigest)
from pwanimate.ai.gateway import AIGateway
from pwanimate.ai.gateway.types import LLMRequest, ChatMessage
logger = logging.getLogger(__name__)


def parse_object(text):
    """Tolerant JSON object parse: strict first, then salvage the outermost
    object when a small model wraps the payload in prose or truncates it."""
    text = (text or '').strip()
    if text.startswith('```'):
        text = text.split('\n', 1)[1].rsplit('```', 1)[0].strip()
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        start = text.find('{')
        if start == -1:
            raise
        end = text.rfind('}')
        candidate = text[start:end + 1] if end > start else text[start:]
        try:
            value = json.loads(candidate, strict=False)
        except json.JSONDecodeError:
            cut = max(candidate.rfind(','), candidate.rfind('{'))
            if cut <= 0:
                raise
            value = json.loads(candidate[:cut] + '}', strict=False)
    if not isinstance(value, dict):
        raise ValueError('Expected a JSON object')
    return value


class StudyMemoryService:
    @staticmethod
    def preference(user):
        return SparkMemoryPreference.objects.get_or_create(user=user)[0]

    @classmethod
    def refresh(cls, conversation_id, gateway=None):
        # Raw transcript summaries are quarantined. Only explicit approved facts
        # may be reused; old queued jobs cannot call a provider or publish a digest.
        return False

    @classmethod
    def welcome(cls, user):
        return {'greeting': f'Hello, {user.get_short_name() or user.get_username()}.',
                'question': 'What would you like to work on today?', 'suggestions': [], 'memory': False}

    @classmethod
    def forget(cls, user):
        cls.preference(user)
        with transaction.atomic():
            pref = SparkMemoryPreference.objects.select_for_update().get(user=user)
            pref.reset_at = timezone.now()
            pref.revision += 1
            pref.extraction_cutoffs = {}
            pref.save(update_fields=['reset_at', 'revision', 'extraction_cutoffs'])
            from pwanimate.models import SparkPersonalMemory
            SparkPersonalMemory.objects.filter(user=user).delete()
            SparkConversationDigest.objects.filter(conversation__user=user).delete()
            PwanimateMessageEmbedding.objects.filter(user=user).delete()
