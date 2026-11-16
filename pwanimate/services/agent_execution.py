"""Shared agent-run continuation executor.

Used by both the synchronous API path (JSON clients) and the Celery worker
(SSE clients). Execution always writes to the durable journal and plan-step
tables, so a browser disconnect never loses the run: the journal-tail SSE
stream and the agent-runs endpoints can reconstruct everything afterwards.
"""
from pwanimate.ai.completion import is_complete
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
        from pwanimate.models import PwanimateConversation
        PwanimateConversation.objects.select_for_update().get(pk=run.user_message.conversation_id,user=user)
        locked = SparkAgentRun.objects.select_for_update().filter(pk=run.pk, user=user).first()
        if locked is None:
            raise RunError('This task is unavailable.')
        from pwanimate.services.study_context import fence_run
        fence_run(user,locked)
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

        locked.result_message = None
        locked.attempt_id = attempt_id
        locked.lease_expires_at = timezone.now() + timedelta(seconds=RUN_LEASE_SECONDS)
        locked.continuation = {
            'query_suffix': query_suffix, 'note': note, 'kind': kind,
            'interaction_id': str(resolved.id) if resolved else (
                str(interaction.id) if interaction else None),
            'via': via, 'answer_message_id': answer_message_id,
        }
        locked.save(update_fields=['attempt_id', 'lease_expires_at', 'continuation', 'result_message', 'updated_at'])
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
    from pwanimate.orchestrator.activity import event, public_event, public_events
    from pwanimate.context.user_context import UserContextService
    from pwanimate.services.conversation import ConversationService

    activity = []
    downstream = progress

    def record(value):
        if isinstance(value, str):
            from pwanimate.orchestrator.activity import STAGES
            value = event('stage', '', event_id='stage-' + value) if value in STAGES else None
        value = public_event(value)
        if value is None or public_events(activity + [value]) == public_events(activity):
            return
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

    from pwanimate.services.run_snapshots import decode_snapshot, SnapshotCompatibilityError
    decoded = decode_snapshot(run.snapshot)
    if decoded.get('user_message_id') != run.user_message_id:
        raise SnapshotCompatibilityError('This task snapshot does not own its original request.')
    from pwanimate.services.study_context import fence_run
    fence_run(run.user,run)
    continuation = dict(run.continuation or {})
    if (run.state or {}).get('wp4_consent'):
        from pwanimate.services.study_context import resolve,StudyConflict
        selected=resolve(run.user,run.user_message.conversation)
        if selected.generation!=run.state.get('wp4_binding_generation',selected.generation):
            raise StudyConflict('Session selection changed before this lesson executed.')
        # Decision execution is already claimed. Establish a post-decision
        # source once, retaining the original run/user-message identity.
        with transaction.atomic():
            boundary_run=SparkAgentRun.objects.select_for_update().get(pk=run.pk,user=run.user)
            if not boundary_run.snapshot.get('execution_boundary_id'):
                source=ConversationService.persist_user_message(boundary_run.user_message.conversation,boundary_run.snapshot['query'])
                if boundary_run.state.get('wp4_document_id'):
                    from pwanimate.services.unit_learning import UnitLearningService
                    UnitLearningService.process_message(source.pk,document_id=boundary_run.state['wp4_document_id'])
                source.task='academic_execution'
                source.save(update_fields=['task'])
                boundary_run.snapshot={**boundary_run.snapshot,'execution_boundary_id':source.pk}
                boundary_run.save(update_fields=['snapshot','updated_at'])
            run.snapshot=boundary_run.snapshot
    snapshot = decode_snapshot(run.snapshot, require_execution_boundary=bool((run.state or {}).get('wp4_consent')))
    snapshot['user'] = run.user
    if continuation.get('query_suffix'):
        snapshot['query'] = (snapshot.get('query') or '') + continuation['query_suffix']
    from pwanimate.services.conversation_memory import ConversationMemoryService
    snapshot['conversation_memory'] = ConversationMemoryService.search(run.user, snapshot.get('query', ''))
    from pwanimate.services.unit_learning import UnitLearningService
    snapshot['academic_unit_context'] = UnitLearningService.safe_context(run.user, snapshot.get('query', ''))
    conversation = run.user_message.conversation if run.user_message_id else None
    snapshot['tracking_state'] = UnitLearningService.tracking_state(run.user, conversation=conversation)
    from pwanimate.services.execution_source import effective_execution_source
    execution_source = effective_execution_source(run)
    req = OrchestrationRequest(**snapshot)
    from pwanimate.orchestrator.timing import TimingRecorder
    req.timing = TimingRecorder()
    req.timing.strategy = 'durable_continuation'
    if req.study_mode and conversation:
        from pwanimate.services.study_context import resolve
        from pwanimate.services.study_session import StudySessionService
        selected = resolve(run.user, conversation)
        if selected.session and selected.session.status == 'active':
            req.study_context = StudySessionService.build_study_orchestration_context(
                session=selected.session, user=run.user)
        else:
            req.study_mode = False  # Snapshot reconstruction never resumes a session.
    if req.context_resources:
        from pwanimate.orchestrator.document_context import DocumentTurnContext
        req.document_context = DocumentTurnContext.resolve(req.user, req.context_resources, req.query)
    req.user_context = UserContextService().build(run.user)

    prior_activity = public_events(run.activity)
    for value in prior_activity:
        if value.get('kind') == 'clarification': value['status'] = 'answered'
        if value.get('kind') == 'approval':
            value['status'] = 'completed'
            value['actions'] = AgentRunService.serialize(run).get('actions', [])
    activity[:0] = prior_activity
    if run.snapshot.get('execution_boundary_id'):
        record(event('plan','Tracking decision recorded.',event_id='tracking-consent',status='completed',run_id=str(run.pk)))
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

    # Cancellation may arrive during final generation, after the earlier check.
    fresh = SparkAgentRun.objects.get(pk=run.pk)
    if fresh.cancel_requested:
        _finish_cancelled(run, activity, record)
        return {'cancelled': True, 'run_id': str(run.id)}
    completed = is_complete(response.finish_reason, response.answer)
    response.metadata['perf'] = req.timing.snapshot()
    awaiting = bool((response.metadata or {}).get('awaiting_approval')
                    or (response.metadata or {}).get('awaiting_clarification'))
    if not awaiting and not completed:
        record(event('plan', 'This response is incomplete; saved partial text is not completed learning.',
                     event_id='response-incomplete', status='failed'))
    # Fence the attempt and persist its result atomically. No model I/O under locks.
    with transaction.atomic():
        from pwanimate.models import PwanimateConversation
        PwanimateConversation.objects.select_for_update().get(
            pk=run.user_message.conversation_id, user=run.user)
        current = SparkAgentRun.objects.select_for_update().get(pk=run.pk)
        if current.attempt_id != run.attempt_id or current.status not in {'running', 'waiting'}:
            raise RunError('This execution attempt is no longer current.')
        if current.cancel_requested:
            _finish_cancelled(run, activity, record)
            return {'cancelled': True, 'run_id': str(run.id)}
        message = ConversationService.persist_assistant_message(
            conversation=run.user_message.conversation, content=response.answer,
            citations=response.citations, sources=response.sources,
            provider=response.provider, model_id=response.model,
            task=('teaching_pause' if response.metadata.get('teaching') else 'agent_pause') if awaiting else 'agent', activity=activity,
            prompt_tokens=response.prompt_tokens or 0,
            completion_tokens=response.completion_tokens or 0,
            total_tokens=response.total_tokens or 0,
            teaching_turn_id=response.metadata.get('teaching_turn_id'),
            teaching_complete=bool(completed and response.metadata.get('teaching_complete', False)),
            academic_source_id=execution_source.pk,
            academic_complete=bool(not awaiting and is_complete(response.finish_reason, response.answer)))

        # A resumed run that pauses again updates the SAME run row
        # (AgentRunService.pause is keyed on the original user message), so
        # 'awaiting' — not a new run id — decides whether this attempt ended in
        # another wait or a terminal state.
        try:
            agent_plans.sync_steps(run, prepared.get('checklist'), None)
        except Exception:
            logger.warning('Could not sync plan steps for run %s', run.id, exc_info=True)
        agent_journal.sync_activity(run, activity)
        # Publish final status and result pointer in one commit; a journal reader
        # must never observe "completed" without its durable answer.
        with transaction.atomic():
            locked = SparkAgentRun.objects.select_for_update().get(pk=run.pk)
            if not awaiting and locked.status == SparkAgentRun.STATUS_RUNNING:
                AgentRunService.transition(locked, SparkAgentRun.STATUS_COMPLETED if completed else SparkAgentRun.STATUS_INTERRUPTED)
            locked.state = {**(locked.state or {}), 'result_completion': {
                'message_id': message.pk, 'finish_reason': response.finish_reason,
                'response_complete': completed}, 'result_teaching': {
                'message_id': message.pk, 'turn_id': response.metadata.get('teaching_turn_id')}}
            locked.save(update_fields=['state', 'updated_at'])
            SparkAgentRun.objects.filter(pk=run.pk).update(activity=activity, result_message=message)

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


@transaction.atomic
def mark_failed(run, error):
    """Fail only the still-current attempt; serialize against explicit retries."""
    current = SparkAgentRun.objects.select_for_update().get(pk=run.pk)
    if current.attempt_id != run.attempt_id:
        return
    if current.status not in {SparkAgentRun.STATUS_COMPLETED,
                              SparkAgentRun.STATUS_CANCELLED,
                              SparkAgentRun.STATUS_EXPIRED}:
        AgentRunService.transition(current, SparkAgentRun.STATUS_FAILED)
        try:
            agent_journal.append(current, 'run_failed', {'error': type(error).__name__})
        except Exception:
            logger.warning('Could not journal failed run %s', current.pk, exc_info=True)
