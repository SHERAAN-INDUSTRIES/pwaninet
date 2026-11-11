"""Dynamic plan steps mirrored from the planner's reconciled checklist.

The planner still thinks in checklist items; this service projects them into
durable SparkAgentPlanStep rows keyed by a stable per-run slug so revisions
update steps in place. Steps the planner drops are marked skipped rather than
deleted, preserving what the run actually attempted.
"""
import re
import unicodedata
from django.db import transaction
from django.utils import timezone
from pwanimate.models import SparkAgentPlanStep

MAX_STEPS = 8  # mirrors StudyAgent.MAX_CHECKLIST_ITEMS

_STATUS_MAP = {
    'pending': SparkAgentPlanStep.STATUS_PENDING,
    'doing': SparkAgentPlanStep.STATUS_RUNNING,
    'running': SparkAgentPlanStep.STATUS_RUNNING,
    'done': SparkAgentPlanStep.STATUS_COMPLETED,
    'completed': SparkAgentPlanStep.STATUS_COMPLETED,
    'complete': SparkAgentPlanStep.STATUS_COMPLETED,
    'blocked': SparkAgentPlanStep.STATUS_BLOCKED,
    'failed': SparkAgentPlanStep.STATUS_FAILED,
    'skipped': SparkAgentPlanStep.STATUS_SKIPPED,
    'cancelled': SparkAgentPlanStep.STATUS_CANCELLED,
}
_TERMINAL_STEP_STATUSES = [
    SparkAgentPlanStep.STATUS_COMPLETED,
    SparkAgentPlanStep.STATUS_SKIPPED,
    SparkAgentPlanStep.STATUS_CANCELLED,
]


def _key(label, index):
    slug = re.sub(r'[^a-z0-9]+', '-', unicodedata.normalize(
        'NFKD', str(label)).encode('ascii', 'ignore').decode().lower()).strip('-')
    return (slug or f'step-{index + 1}')[:120]


def sync_steps(run, checklist, observed=None):
    """Upsert plan steps from a planner checklist. Returns the step rows.

    `checklist` items look like {'label','status','finding'}. The last observed
    tool outcome is attached as evidence; completion truth still comes from
    the reconciled checklist, which the planner only marks done against
    recorded observations.
    """
    if not isinstance(checklist, list):
        return []
    observed = observed or []
    steps = []
    with transaction.atomic():
        seen_keys = set()
        for index, item in enumerate(checklist[:MAX_STEPS]):
            if not isinstance(item, dict):
                continue
            label = str(item.get('label') or '').strip()[:200]
            if not label:
                continue
            base = _key(label, index)
            key, n = base, 2
            while key in seen_keys:
                key = f'{base}-{n}'
                n += 1
            seen_keys.add(key)
            status = _STATUS_MAP.get(str(item.get('status') or 'pending'),
                                     SparkAgentPlanStep.STATUS_PENDING)
            finding = str(item.get('finding') or '').strip()[:400]
            evidence = {}
            if observed:
                last = observed[-1]
                evidence = {'last_tool': last.get('tool'),
                            'last_success': (last.get('result') or {}).get('success')}
            step, _ = SparkAgentPlanStep.objects.update_or_create(
                run=run, key=key,
                defaults={'seq': index, 'title': label, 'status': status,
                          'finding': finding if status in {
                              SparkAgentPlanStep.STATUS_COMPLETED,
                              SparkAgentPlanStep.STATUS_BLOCKED} else '',
                          'evidence': evidence})
            steps.append(step)
        # Steps absent from the latest revision are superseded, not deleted.
        SparkAgentPlanStep.objects.filter(run=run).exclude(
            key__in=seen_keys).exclude(
            status__in=_TERMINAL_STEP_STATUSES).update(
            status=SparkAgentPlanStep.STATUS_SKIPPED, updated_at=timezone.now())
    return steps


def serialize_steps(run):
    return [{'id': str(s.id), 'key': s.key, 'seq': s.seq, 'title': s.title,
             'status': s.status, 'finding': s.finding, 'depends_on': s.depends_on}
            for s in SparkAgentPlanStep.objects.filter(run=run).order_by('seq', 'created_at')]
