"""Recovery for Spark agent runs whose execution attempt went stale.

Policy: a run left 'running' past its lease means the worker/request thread
died mid-execution. The attempt is marked 'interrupted' — never silently
re-run, because tool side effects may already have happened. The student can
then explicitly retry (which re-dispatches the stored continuation) or start
over. Waiting runs past expires_at are marked 'expired' lazily here too.
"""
import logging
from django.db import transaction
from django.utils import timezone
from pwanimate.models import SparkAgentRun, SparkAgentInteraction
from pwanimate.services import agent_journal
from pwanimate.services.agent_runs import AgentRunService

logger = logging.getLogger(__name__)


def recover_stale_runs(limit=100):
    """Transition stale running/waiting runs. Returns counts."""
    now = timezone.now()
    interrupted = expired = 0
    stale = SparkAgentRun.objects.filter(
        status=SparkAgentRun.STATUS_RUNNING,
        lease_expires_at__lt=now,
    )[:limit]
    for run in stale:
        try:
            with transaction.atomic():
                locked = SparkAgentRun.objects.select_for_update().get(pk=run.pk)
                if locked.status != SparkAgentRun.STATUS_RUNNING:
                    continue
                AgentRunService.transition(locked, SparkAgentRun.STATUS_INTERRUPTED)
                agent_journal.append(locked, 'run_interrupted', {
                    'attempt_id': locked.attempt_id,
                    'reason': 'lease_expired'})
            interrupted += 1
        except Exception:
            logger.warning('Could not recover stale agent run %s', run.id, exc_info=True)
    waiting = SparkAgentRun.objects.filter(
        status=SparkAgentRun.STATUS_WAITING, expires_at__lt=now,
    )[:limit]
    for run in waiting:
        try:
            with transaction.atomic():
                locked = SparkAgentRun.objects.select_for_update().get(pk=run.pk)
                if locked.status != SparkAgentRun.STATUS_WAITING:
                    continue
                AgentRunService.transition(locked, SparkAgentRun.STATUS_EXPIRED)
                SparkAgentInteraction.objects.filter(
                    run=locked, status=SparkAgentInteraction.STATUS_PENDING,
                ).update(status=SparkAgentInteraction.STATUS_EXPIRED,
                         resolved_at=now)
                agent_journal.append(locked, 'run_expired', {})
            expired += 1
        except Exception:
            logger.warning('Could not expire agent run %s', run.id, exc_info=True)
    return {'interrupted': interrupted, 'expired': expired}
