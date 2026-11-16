"""Durable fact extraction; legacy raw-transcript indexing stays disabled."""

import logging
from typing import Any, Dict

from celery import shared_task
logger = logging.getLogger(__name__)


@shared_task(
    bind=True,
    max_retries=3,
    default_retry_delay=60,
    name="pwanimate.tasks.conversation_memory.embed_message",
)
def embed_message(self, message_id: int) -> Dict[str, Any]:
    # No raw transcript indexing, even if a legacy switch remains enabled.
    return {'status': 'personal_fact_approval_required', 'message_id': message_id}


@shared_task(bind=True, max_retries=3, default_retry_delay=30,
    acks_late=True, reject_on_worker_lost=True, queue='docs_queue',
    name='pwanimate.tasks.conversation_memory.extract_memories')
def extract_memories(self, assistant_id, revision):
    # Backward-compatible old broker deliveries enter the durable pipeline.
    from pwanimate.models import PwanimateMessage
    from pwanimate.services.memory_dispatch import schedule
    message = PwanimateMessage.objects.filter(pk=assistant_id).first()
    job = schedule(message, revision=revision) if message else None
    return {'status': 'scheduled' if job else 'ineligible'}


@shared_task(name='pwanimate.tasks.conversation_memory.process_memory_dispatch',
    acks_late=True, reject_on_worker_lost=True, max_retries=0, queue='docs_queue')
def process_memory_dispatch(job_id, token):
    from pwanimate.services.memory_dispatch import process
    return process(job_id, token)


@shared_task(name='pwanimate.tasks.conversation_memory.recover_memory_dispatch', max_retries=0)
def recover_memory_dispatch():
    from pwanimate.services.memory_dispatch import recover
    from django.db import DatabaseError
    try:
        return recover()
    except DatabaseError:
        # Pre-migration/DB outage: no extraction is attempted; next beat retries.
        logger.exception('memory_dispatch recovery_database_unavailable')
        return {'database_unavailable': True}
