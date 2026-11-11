"""Durable execution of claimed Spark agent runs.

The HTTP layer claims a waiting run (status -> running, attempt_id + lease)
inside a short transaction, commits, then dispatches this task. The worker
performs all LLM/tool work outside any lock and writes progress to the
durable journal, so a browser disconnect — or a lost request thread — never
loses the execution.

Exactly-once is approximated, not assumed: the claim is atomic, the attempt
id is verified before work starts, tool writes keep their own idempotency
keys, and a stale lease lets recovery mark the attempt interrupted instead of
replaying blindly.
"""
import logging
import uuid

from celery import shared_task

logger = logging.getLogger(__name__)


@shared_task(
    bind=True,
    name="pwanimate.tasks.execute_agent_run",
    max_retries=0,
    acks_late=True,
    reject_on_worker_lost=True,
)
def execute_agent_run(self, run_id: str, attempt_id: str) -> dict:
    from django.utils import timezone
    from pwanimate.models import SparkAgentRun
    from pwanimate.services import agent_execution, agent_journal
    from pwanimate.services.agent_runs import AgentRunService

    run = SparkAgentRun.objects.filter(pk=run_id).first()
    if run is None:
        return {"status": "missing", "run_id": run_id}
    # Only the claimed attempt may execute; anything else is a duplicate
    # delivery or a superseded claim — drop it safely.
    if run.attempt_id != attempt_id or run.status != SparkAgentRun.STATUS_RUNNING:
        return {"status": "skipped", "run_id": run_id,
                "run_status": run.status, "attempt_id": run.attempt_id}
    # Duplicate-delivery guard: acks_late redelivery (or a stray second
    # dispatch) must not run the agent loop twice. The journal marker is
    # deduplicated under the run row lock, so exactly one delivery wins.
    marker = agent_journal.append(run, 'run_execution_started', {'attempt_id': attempt_id})
    if marker is None:
        return {"status": "duplicate", "run_id": run_id, "attempt_id": attempt_id}
    try:
        payload = agent_execution.execute_claimed(run)
        return {"status": "ok", "run_id": run_id,
                "cancelled": bool(payload.get("cancelled")),
                "message_id": payload.get("message_id")}
    except Exception as exc:
        logger.exception("Agent run execution failed run=%s attempt=%s", run_id, attempt_id)
        try:
            agent_execution.mark_failed(run, exc)
        except Exception:
            logger.exception("Could not mark agent run failed run=%s", run_id)
        try:
            agent_journal.append(run, "run_failed", {"error": type(exc).__name__})
        except Exception:
            pass
        return {"status": "error", "run_id": run_id, "error": type(exc).__name__}


@shared_task(name="pwanimate.tasks.recover_agent_runs")
def recover_agent_runs() -> dict:
    """Periodic sweep: interrupt lease-expired attempts, expire stale waits."""
    from pwanimate.services import agent_recovery
    try:
        return agent_recovery.recover_stale_runs()
    except Exception:
        logger.exception("Agent run recovery sweep failed")
        return {"interrupted": 0, "expired": 0, "error": True}


def dispatch_run(run_id, attempt_id):
    """Queue execution after commit; fall back to an inline thread.

    Mirrors the codebase's existing Celery-dispatch-with-inline-fallback
    pattern (study checkpoints): when no broker/worker is reachable the run
    still executes, just inside a daemon thread of the request process.
    """
    from django.db import transaction

    def _go():
        try:
            execute_agent_run.delay(str(run_id), attempt_id)
        except Exception:
            logger.warning("Celery dispatch unavailable for agent run %s; running inline",
                           run_id, exc_info=True)
            import threading
            from pwanimate.services import agent_execution
            from pwanimate.models import SparkAgentRun

            def _inline():
                try:
                    fresh = SparkAgentRun.objects.get(pk=run_id)
                    if fresh.attempt_id == attempt_id and fresh.status == 'running':
                        agent_execution.execute_claimed(fresh)
                except Exception:
                    logger.exception("Inline agent run execution failed run=%s", run_id)
                    try:
                        agent_execution.mark_failed(fresh, None)
                    except Exception:
                        pass

            threading.Thread(target=_inline, name=f"spark-run-{run_id}", daemon=True).start()

    transaction.on_commit(_go)
