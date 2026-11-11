"""Durable append-only journal of public Spark execution events.

The journal is the authoritative replay source for activity trails: every
public event emitted during a run is persisted with a per-run sequence number
so any client — on any app process — can reconnect and receive exactly the
events it missed. Payloads are the same sanitized public events the SSE
stream emits; nothing private is journaled here.
"""
import hashlib
import json
from django.db import transaction
from django.db.models import Max
from pwanimate.models import SparkAgentEvent, SparkAgentRun


def _dedupe_key(payload):
    try:
        raw = json.dumps(payload, sort_keys=True, default=str)
    except (TypeError, ValueError):
        raw = repr(payload)
    return hashlib.sha1(raw.encode('utf-8')).hexdigest()


def append(run, event_type, payload):
    """Append one journaled event. Returns the event or None if deduplicated.

    seq is allocated under a row lock on the run so concurrent emitters
    (request thread, Celery worker, recovery) cannot collide.
    """
    dedupe = _dedupe_key({'type': event_type, 'payload': payload})
    with transaction.atomic():
        locked = SparkAgentRun.objects.select_for_update().only('pk').get(pk=run.pk)
        if SparkAgentEvent.objects.filter(run=locked, dedupe_key=dedupe).exists():
            return None
        last = SparkAgentEvent.objects.filter(run=locked).aggregate(m=Max('seq'))['m'] or 0
        return SparkAgentEvent.objects.create(
            run=locked, seq=last + 1, type=event_type, payload=payload,
            dedupe_key=dedupe)


def sync_activity(run, activity):
    """Flush an in-memory activity list (pause/finalize path) into the journal.

    Idempotent via dedupe_key, so events already journaled live during a
    resumed run are not duplicated.
    """
    for entry in activity or []:
        if isinstance(entry, dict) and entry.get('type') == 'activity':
            append(run, 'activity', entry)


def events_after(user, run_id, after_seq=0, limit=500):
    """Owner-scoped replay: events with seq > after_seq, oldest first."""
    run = SparkAgentRun.objects.filter(pk=run_id, user=user).first()
    if run is None:
        return None, []
    qs = (SparkAgentEvent.objects.filter(run=run, seq__gt=int(after_seq or 0))
          .order_by('seq')[:max(1, min(int(limit or 500), 1000))])
    return run, list(qs)


def serialize_event(event):
    return {'seq': event.seq, 'type': event.type, 'payload': event.payload,
            'at': event.created_at.isoformat()}
