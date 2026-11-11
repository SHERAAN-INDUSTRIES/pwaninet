"""Shared agent-run continuation executor.

Used by both the synchronous API path (JSON clients) and the Celery worker
(SSE clients). Execution always writes to the durable journal and plan-step
tables, so a browser disconnect never loses the run: the journal-tail SSE
stream and the agent-runs endpoints can reconstruct everything afterwards.
"""
import logging
import time
import uuid
from datetime import timedelta
from django.db import transaction
from django.utils import timezone
from pwanimate.models import SparkAgentRun, SparkAgentInteraction
from pwanimate.services.agent_runs import AgentRunService, RunError, RUN_LEASE_SECONDS
from pwanimate.services import agent_journal, agent_plans

logger = logging.getLogger(__name__)


def claim_run(user, run, *, interaction=None, raw_answer=None, legacy_text='',
              note='', kind='clarification', via='embedded', attempt_id=None):
    """Atomically resolve input and move a waiting run to running.

    One short transaction does all of: run lock + ownership/status/expiry
    checks, interaction validation+resolution (exactly-once), answer-message
    persistence, attempt/lease capture, continuation payload, and the
    waiting -> running transition. The caller commits this BEFORE any LLM
    work — the row lock is never held during generation.

    Returns (run, attempt_id).
    """
    from pwanimate.services import interactions as interaction_service
    from pwanimate.services.conversation import ConversationService

    attempt_id = attempt_id or uuid.uuid4().hex
    with transaction.atomic():
        locked = SparkAgentRun.objects.select_for_update().filter(pk=run.pk, user=user).first()
        if locked is None:
            raise RunError('This task is unavailable.')
        if locked.status != SparkAgentRun.STATUS_WAITING:
            raise RunError('This task has already continued. Refresh the conversation to see its status.')
        if locked.action_ids:
            raise RunError('This task is waiting for action approval, not an answer.')
        if locked.expires_at <= timezone.now():
            AgentRunService.transition(locked, SparkAgentRun.STATUS_EXPIRED)
            raise RunError('This task expired. Ask Spark again to get a fresh answer.')

        resolved = None
        answer_text = ''
        if interaction is not None:
            resolved = interaction_service.resolve(
                locked, interaction.id, raw_answer, submitted_via=via)
            answer_text = interaction_service.answer_text(resolved)
            query_suffix = interaction_service.answer_suffix(resolved)
        elif legacy_text:
            answer_text = legacy_text
            query_suffix = '\n\nStudent clarification: ' + legacy_text[:1000]
        else:
            query_suffix = ''

        answer_message_id = None
        if answer_text:
            try:
                message = ConversationService.persist_user_message(
                    locked.user_message.conversation, answer_text)
                answer_message_id = message.id if message else None
            except Exception:
                logger.warning('Could not persist answer message for run %s', locked.id, exc_info=True)

        locked.attempt_id = attempt_id
        locked.lease_expires_at = timezone.now() + timedelta(seconds=RUN_LEASE_SECONDS)
        locked.continuation = {
            'query_suffix': query_suffix, 'note': note, 'kind': kind,
            'interaction_id': str(resolved.id) if resolved else (
                str(interaction.id) if interaction else None),
            'via': via, 'answer_message_id': answer_message_id,
        }
        locked.save(update_fields=['attempt_id', 'lease_expires_at', 'continuation', 'updated_at'])
        AgentRunService.transition(locked, SparkAgentRun.STATUS_RUNNING)
    agent_journal.append(locked, 'run_resumed', {
        'attempt_id': attempt_id, 'kind': kind,
        'interaction_id': str(resolved.id) if resolved else None})
    if resolved is not None:
        agent_journal.append(locked, 'clarification_answered', {
            'interaction_id': str(resolved.id), 'via': via})
    return locked, attempt_id


def cancel_requested(run):
    return SparkAgentRun.objects.filter(pk=run.pk).values_list('cancel_requested', flat=True).first()


def execute_claimed(run, *, progress=None, token_callback=None):
    """Run the continuation for a run already claimed (status='running').

    Returns the response payload dict. Raises on unexpected failure — the
    caller decides how to mark the run (the Celery task marks it failed;
    the sync API path maps errors to HTTP responses).
    """
    from pwanimate.api.views import PwanimateChatView
    from pwanimate.orchestrator.types import OrchestrationRequest
    from pwanimate.orchestrator.agent import StudyAgent
    from pwanimate.orchestrator.activity import event
    from pwanimate.context.user_context import UserContextService
    from pwanimate.services.conversation import ConversationService

    activity = []
    downstream = progress

    def record(value):
        if isinstance(value, dict) and value.get('type') == 'activity':
            if len(activity) < 200:
                activity.append(value)
            # Live journal: clients tailing the run see progress in ~1s.
            try:
                agent_journal.append(run, 'activity', value)
            except Exception:
                logger.warning('Could not journal agent event for run %s', run.id, exc_info=True)
        if downstream:
            downstream(value)

    continuation = dict(run.continuation or {})
    snapshot = dict(run.snapshot or {})
    snapshot['user'] = run.user
    if continuation.get('query_suffix'):
        snapshot['query'] = (snapshot.get('query') or '') + continuation['query_suffix']
    req = OrchestrationRequest(**snapshot)
    req.user_context = UserContextService().build(run.user)

    prior_activity = list(run.activity or [])
    activity[:0] = prior_activity
    record(event('plan', continuation.get('note') or 'Your answer is recorded. Continuing the task.',
                 status='completed', run_id=str(run.id),
                 actions=AgentRunService.serialize(run).get('actions', [])))

    orchestrator = PwanimateChatView().get_orchestrator()
    start = time.perf_counter()

    def cancel_check():
        return bool(cancel_requested(run))

    def on_checklist(checklist, observed):
        try:
            agent_plans.sync_steps(run, checklist, observed)
        except Exception:
            logger.warning('Could not sync plan steps for run %s', run.id, exc_info=True)

    prepared = StudyAgent(orchestrator.gateway, orchestrator.tool_registry).run(
        req, orchestrator._request_metadata(req), record,
        resume_state=run.state, cancel_check=cancel_check,
        run=run, on_checklist=on_checklist)

    fresh = SparkAgentRun.objects.get(pk=run.pk)
    if fresh.cancel_requested or (prepared or {}).get('cancelled'):
        _finish_cancelled(run, activity, record)
        return {'cancelled': True, 'run_id': str(run.id)}
    if not prepared:
        raise RunError('Could not continue this task. Your approval outcomes are saved; no action will be repeated automatically.')

    response = orchestrator.finish_agent(req, prepared, start, record, token_callback)

    # Interim "say" bubbles persist before the final answer, as on first turn.
    for evt in activity:
        if isinstance(evt, dict) and evt.get('kind') == 'say':
            say_text = str(evt.get('text') or '').strip()
            if say_text:
                try:
                    ConversationService.persist_assistant_message(
                        conversation=run.user_message.conversation,
                        content=say_text, provider=response.provider,
                        model_id=response.model, task='agent_interim')
                except Exception:
                    logger.exception('Could not persist interim Spark message run=%s', run.id)

    awaiting = bool((response.metadata or {}).get('awaiting_approval')
                    or (response.metadata or {}).get('awaiting_clarification'))
    message = ConversationService.persist_assistant_message(
        conversation=run.user_message.conversation, content=response.answer,
        citations=response.citations, sources=response.sources,
        provider=response.provider, model_id=response.model,
        task='agent_pause' if awaiting else 'agent', activity=activity,
        prompt_tokens=response.prompt_tokens or 0,
        completion_tokens=response.completion_tokens or 0,
        total_tokens=response.total_tokens or 0)

    # A resumed run that pauses again updates the SAME run row
    # (AgentRunService.pause is keyed on the original user message), so
    # 'awaiting' — not a new run id — decides whether this attempt ended in
    # another wait or a terminal state.
    current = SparkAgentRun.objects.get(pk=run.pk)
    try:
        agent_plans.sync_steps(run, prepared.get('checklist'), None)
    except Exception:
        logger.warning('Could not sync plan steps for run %s', run.id, exc_info=True)
    if awaiting:
        agent_journal.sync_activity(run, activity)
        SparkAgentRun.objects.filter(pk=run.pk).update(
            activity=activity, result_message=message)
    else:
        agent_journal.sync_activity(run, activity)
        if current.status == SparkAgentRun.STATUS_RUNNING:
            AgentRunService.transition(current, SparkAgentRun.STATUS_COMPLETED)
        SparkAgentRun.objects.filter(pk=run.pk).update(
            activity=activity, result_message=message)

    payload = response.to_dict()
    payload.update({'message_id': message.id,
                    'conversation_id': str(message.conversation_id),
                    'activity': activity, 'run_id': str(run.id)})
    return payload


def _finish_cancelled(run, activity, record):
    """Persist the cancelled outcome: journal, activity, terminal status."""
    from pwanimate.services.conversation import ConversationService
    record({'type': 'activity', 'id': 'run-cancelled', 'kind': 'plan',
            'text': 'Stopped — you cancelled this task.', 'status': 'completed',
            'at': timezone.now().isoformat(), 'run_id': str(run.id)})
    try:
        agent_journal.append(run, 'run_cancelled', {'run_id': str(run.id)})
        agent_journal.sync_activity(run, activity)
        ConversationService.persist_assistant_message(
            conversation=run.user_message.conversation,
            content='Stopped — you cancelled this task.',
            provider='', model_id='', task='agent', activity=activity)
    except Exception:
        logger.exception('Could not persist cancellation for run %s', run.id)
    current = SparkAgentRun.objects.get(pk=run.pk)
    if current.status not in {SparkAgentRun.STATUS_COMPLETED,
                              SparkAgentRun.STATUS_FAILED,
                              SparkAgentRun.STATUS_CANCELLED,
                              SparkAgentRun.STATUS_EXPIRED}:
        AgentRunService.transition(current, SparkAgentRun.STATUS_CANCELLED)


def mark_failed(run, error):
    """Terminal failure path shared by sync and worker execution."""
    try:
        agent_journal.append(run, 'run_failed', {'error': type(error).__name__})
    except Exception:
        pass
    current = SparkAgentRun.objects.get(pk=run.pk)
    if current.status not in {SparkAgentRun.STATUS_COMPLETED,
                              SparkAgentRun.STATUS_CANCELLED,
                              SparkAgentRun.STATUS_EXPIRED}:
        try:
            AgentRunService.transition(current, SparkAgentRun.STATUS_FAILED)
        except Exception:
            SparkAgentRun.objects.filter(pk=run.pk).update(status='failed')
