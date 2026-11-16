"""Database-backed bounded delivery for the existing memory extractor."""
import logging
import random
import uuid
import threading
from datetime import timedelta
from django.db import transaction, close_old_connections
from django.db.models import Q
from django.utils import timezone
from pwanimate.models import SparkMemoryDispatch, SparkMemoryPreference
from pwanimate.services.personal_memory import enabled

logger = logging.getLogger(__name__)
MAX_ATTEMPTS = 8
LEASE_SECONDS = 300
WAKE_SLOTS = threading.BoundedSemaphore(2)
EXCLUDED = {'agent_pause', 'agent_interim', 'teaching_pause'}


def eligible(message, pref, revision=None):
    return bool(message and pref and message.role == 'assistant' and message.task not in EXCLUDED
        and message.conversation.user_id == pref.user_id and enabled(pref)
        and pref.automatic_extraction and pref.automatic_enabled_at
        and (revision is None or revision == pref.revision)
        and message.created_at >= pref.automatic_enabled_at
        and (not pref.reset_at or message.created_at > pref.reset_at))


def schedule(message, *, revision=None, wake=True):
    """Caller message transaction owns durability; no broker or extraction here."""
    pref = SparkMemoryPreference.objects.filter(user_id=message.conversation.user_id).first()
    if not eligible(message, pref, revision):
        return None
    job, _ = SparkMemoryDispatch.objects.get_or_create(source_message_id=message.pk,
        consent_revision=pref.revision, defaults={'user_id':pref.user_id,
            'message':message, 'conversation':message.conversation,
            'source_conversation_id':str(message.conversation_id)})
    if wake:
        transaction.on_commit(lambda: wake_dispatch(str(job.pk)))
    return job


def wake_dispatch(job_id):
    # Best-effort accelerator only. Beat/management recovery is authoritative.
    # Broker I/O must not extend visible request latency; no inline extraction.
    if not WAKE_SLOTS.acquire(blocking=False):
        return
    def send():
        close_old_connections()
        try:
            dispatch(job_id)
        except Exception:
            logger.exception('memory_dispatch wake_failed job=%s', job_id)
        finally:
            close_old_connections()
            WAKE_SLOTS.release()
    try:
        threading.Thread(target=send, name='spark-memory-dispatch', daemon=True).start()
    except Exception:
        WAKE_SLOTS.release()
        logger.exception('memory_dispatch wake_start_failed job=%s', job_id)


def valid(job, *, lock=False):
    # Processing takes the same consent lock as revocation and the extractor.
    qs = SparkMemoryPreference.objects
    if lock:
        qs = qs.select_for_update()
    pref = qs.filter(user_id=job.user_id).first()
    if lock:
        from pwanimate.models import PwanimateMessage, PwanimateConversation
        message = PwanimateMessage.objects.select_for_update().filter(pk=job.message_id).first()
        if message:
            conversation = PwanimateConversation.objects.select_for_update().filter(pk=message.conversation_id).first()
            if conversation is None or conversation.user_id != job.user_id:
                return False
    else:
        message = job.message
    return bool(job.eligibility_version == 1 and message and job.conversation_id and message.pk == job.source_message_id
        and str(message.conversation_id) == job.source_conversation_id
        and message.conversation_id == job.conversation_id
        and message.conversation.user_id == job.user_id
        and eligible(message, pref, job.consent_revision))


def cancel(job, reason='ineligible'):
    job.status, job.last_error, job.finished_at = 'cancelled', reason, timezone.now()
    job.lease_until = None
    job.save()


def retry(job, error):
    job.last_error = error[:80]  # exception class/reason only; never fact text
    job.lease_until = None
    if job.attempts >= MAX_ATTEMPTS:
        job.status, job.finished_at = 'failed', timezone.now()
    else:
        job.status = 'pending'
        delay = min(3600, 15 * 2 ** max(0, job.attempts-1))
        job.next_attempt_at = timezone.now()+timedelta(seconds=delay*random.uniform(.8,1.2))
    job.save()
    logger.warning('memory_dispatch retry job=%s status=%s attempts=%s reason=%s',
        job.pk, job.status, job.attempts, job.last_error)


def dispatch(job_id):
    """Claim/commit before broker I/O. Token also fences delayed deliveries."""
    with transaction.atomic():
        job = SparkMemoryDispatch.objects.select_for_update().filter(pk=job_id).first()
        if not job or job.status in {'completed','cancelled','failed'}:
            return 'terminal'
        now = timezone.now()
        if job.status in {'dispatched','processing'}:
            if job.lease_until and job.lease_until > now:
                return 'busy'
            retry(job, 'lease_expired')
            return job.status
        if job.next_attempt_at > now:
            return 'not_due'
        if not valid(job):
            cancel(job)
            return 'cancelled'
        if job.attempts >= MAX_ATTEMPTS:
            retry(job, 'attempt_limit')
            return 'failed'
        job.attempts += 1
        job.claim_token = uuid.uuid4().hex
        job.status = 'dispatched'
        job.lease_until = now+timedelta(seconds=LEASE_SECONDS)
        job.save()
        token = job.claim_token
    try:
        from pwanimate.tasks.conversation_memory import process_memory_dispatch
        process_memory_dispatch.apply_async(args=[str(job.pk), token], retry=False)
    except Exception as exc:
        with transaction.atomic():
            current = SparkMemoryDispatch.objects.select_for_update().get(pk=job.pk)
            # Worker may already have completed after an ambiguous broker ack.
            if current.claim_token == token and current.status == 'dispatched':
                retry(current, type(exc).__name__)
        return 'retry' if current.status == 'pending' else current.status
    logger.info('memory_dispatch published job=%s attempt=%s', job.pk, job.attempts)
    return 'dispatched'


def process(job_id, token):
    with transaction.atomic():
        job = SparkMemoryDispatch.objects.select_for_update().filter(pk=job_id).first()
        if not job or job.status != 'dispatched' or job.claim_token != token:
            return {'status':'duplicate_or_stale'}
        if not job.lease_until or job.lease_until <= timezone.now():
            retry(job, 'delivery_lease_expired')
            return {'status':job.status}
        if not valid(job, lock=True):
            cancel(job)
            return {'status':'cancelled'}
        job.status = 'processing'
        job.lease_until = timezone.now()+timedelta(seconds=LEASE_SECONDS)
        job.save()
    try:
        # The existing extractor is local/deterministic: no model/provider I/O.
        # Lock + receipt/fact/outcome commit together; crashes roll all back.
        with transaction.atomic():
            job = SparkMemoryDispatch.objects.select_for_update().get(pk=job_id)
            if job.status != 'processing' or job.claim_token != token:
                return {'status':'duplicate_or_stale'}
            if not valid(job, lock=True):
                cancel(job)
                return {'status':'cancelled'}
            from pwanimate.services.persistent_memory import PersistentMemoryService
            stored = PersistentMemoryService.extract(job.message_id, job.consent_revision)
            # Preference remains locked through facts and completion commit.
            job.status, job.finished_at, job.lease_until = 'completed', timezone.now(), None
            job.last_error = ''
            job.save()
        logger.info('memory_dispatch completed job=%s stored=%s', job.pk, stored)
        return {'status':'completed','stored':stored}
    except Exception as exc:
        with transaction.atomic():
            job = SparkMemoryDispatch.objects.select_for_update().get(pk=job_id)
            if job.claim_token == token and job.status == 'processing':
                retry(job, type(exc).__name__)
        return {'status':'retry'}


def recover(limit=50):
    limit = max(1, min(int(limit), 200))
    now = timezone.now()
    due = Q(status='pending', next_attempt_at__lte=now) | Q(
        status__in=['dispatched','processing'], lease_until__lte=now)
    ids = list(SparkMemoryDispatch.objects.filter(due).order_by('next_attempt_at','pk').values_list('pk',flat=True)[:limit])
    counts = {}
    for pk in ids:
        status = dispatch(pk)
        counts[status] = counts.get(status,0)+1
    logger.info('memory_dispatch recovery counts=%s inspected=%s', counts, len(ids))
    return counts
