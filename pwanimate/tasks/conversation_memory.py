"""Celery tasks for indexing persisted conversation messages."""

import logging
from typing import Any, Dict

from celery import shared_task
from django.db import transaction

from pwanimate.ai.embeddings.factory import get_embedding_provider
from pwanimate.models import PwanimateMessage, PwanimateMessageEmbedding

logger = logging.getLogger(__name__)


@shared_task(
    bind=True,
    max_retries=3,
    default_retry_delay=60,
    name="pwanimate.tasks.conversation_memory.embed_message",
)
def embed_message(self, message_id: int) -> Dict[str, Any]:
    try:
        message = PwanimateMessage.objects.select_related("conversation").get(id=message_id)
    except PwanimateMessage.DoesNotExist:
        return {"status": "not_found", "message_id": message_id}

    record, _ = PwanimateMessageEmbedding.objects.get_or_create(
        message=message,
        defaults={"user_id": message.conversation.user_id},
    )
    try:
        provider = get_embedding_provider()
        model_name = provider.get_model_name()
        vector = provider.embed_texts([message.content], task_type="RETRIEVAL_DOCUMENT")[0]
        if len(vector) != provider.get_dimensions():
            raise ValueError(
                f"Embedding dimension mismatch: expected {provider.get_dimensions()}, got {len(vector)}"
            )
        with transaction.atomic():
            record.embedding = vector
            record.embedding_model = model_name
            record.status = "completed"
            record.error = ""
            record.save(update_fields=["embedding", "embedding_model", "status", "error", "updated_at"])
        return {"status": "completed", "message_id": message_id, "model": model_name}
    except Exception as exc:
        record.status = "failed"
        record.error = str(exc)[:255]
        record.save(update_fields=["status", "error", "updated_at"])
        logger.warning("Failed to embed conversation message %s", message_id, exc_info=True)
        raise self.retry(exc=exc)
