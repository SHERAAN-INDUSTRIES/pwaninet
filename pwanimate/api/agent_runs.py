"""Resume an owned Spark task after clarification answers or action decisions.

Two execution modes share one continuation core (services.agent_execution):

- JSON clients run synchronously and get the final payload (backwards compat).
- SSE clients get the run claimed atomically, execution dispatched to the
  durable worker (Celery, inline-thread fallback), and a journal-tail stream
  that survives disconnects — events are replayed from SparkAgentEvent, not
  an in-process queue.
"""
import asyncio
import json
import logging
import time
import uuid
from django.db import transaction
from django.http import StreamingHttpResponse
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework.views import APIView
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.renderers import JSONRenderer
from pwanimate.models import SparkAgentRun, SparkAgentInteraction, PwanimateMessage
from pwanimate.services.agent_runs import AgentRunService, RunError, TransitionError
from pwanimate.services import agent_execution, agent_journal, agent_recovery, agent_plans
from pwanimate.services.interactions import InteractionError

logger = logging.getLogger(__name__)

TAIL_POLL_SECONDS = 0.8
TAIL_KEEPALIVE_SECONDS = 12
TAIL_MAX_SECONDS = 600

_TERMINAL_PAYLOAD_KEYS = ('answer', 'message_id', 'conversation_id', 'citations',
                          'sources', 'provider', 'model', 'metadata')


class AgentRunView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, run_id):
        # Lazily recover stale runs so status reads are truthful.
        try:
            agent_recovery.recover_stale_runs(limit=20)
        except Exception:
            logger.warning('Stale-run recovery failed', exc_info=True)
        run = get_object_or_404(SparkAgentRun, user=request.user, pk=run_id)
        payload = AgentRunService.serialize(run)
        payload['plan'] = agent_plans.serialize_steps(run)
        response = Response(payload)
        response['Cache-Control'] = 'private, no-store'
        return response


class AgentRunEventsView(APIView):
    """Cursor-based journal replay: GET ?after=<seq>&limit=<n>."""
    permission_classes = [IsAuthenticated]

    def get(self, request, run_id):
        try:
            after = int(request.query_params.get('after') or 0)
        except (TypeError, ValueError):
            after = 0
        try:
            limit = int(request.query_params.get('limit') or 500)
        except (TypeError, ValueError):
            limit = 500
        run, events = agent_journal.events_after(request.user, run_id, after, limit)
        if run is None:
            return Response({'error': 'This task is unavailable.'}, status=404)
        payload = {
            'run_id': str(run.id), 'status': run.status, 'wait_reason': run.wait_reason,
            'next_seq': events[-1].seq if events else after,
            'events': [agent_journal.serialize_event(e) for e in events],
        }
        if run.result_message_id:
            payload['result_message_id'] = run.result_message_id
        response = Response(payload)
        response['Cache-Control'] = 'private, no-store'
        return response


class AgentRunCancelView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, run_id):
        run = get_object_or_404(SparkAgentRun, user=request.user, pk=run_id)
        if AgentRunService.is_terminal(run):
            return Response({'error': 'This task already finished.',
                             'run': AgentRunService.serialize(run)}, status=409)
        if run.status == SparkAgentRun.STATUS_WAITING:
            # Nothing is executing: cancel immediately and retire pending input.
            with transaction.atomic():
                locked = SparkAgentRun.objects.select_for_update().get(pk=run.pk)
                if locked.status == SparkAgentRun.STATUS_WAITING:
                    AgentRunService.transition(locked, SparkAgentRun.STATUS_CANCELLED)
                    SparkAgentInteraction.objects.filter(
                        run=locked, status=SparkAgentInteraction.STATUS_PENDING,
                    ).update(status=SparkAgentInteraction.STATUS_CANCELLED,
                             resolved_at=timezone.now())
                    agent_journal.append(locked, 'run_cancelled', {'run_id': str(run.id)})
            run.refresh_from_db()
            return Response({'run': AgentRunService.serialize(run), 'cancelled': True})
        # Running: ask the worker to stop at the next safe boundary.
        SparkAgentRun.objects.filter(pk=run.pk).update(cancel_requested=True)
        agent_journal.append(run, 'cancel_requested', {'run_id': str(run.id)})
        return Response({'run': AgentRunService.serialize(run), 'cancel_requested': True})


class AgentRunRetryView(APIView):
    """Re-dispatch a failed/interrupted run's stored continuation once."""
    permission_classes = [IsAuthenticated]

    def post(self, request, run_id):
        run = get_object_or_404(SparkAgentRun, user=request.user, pk=run_id)
        if run.status not in {SparkAgentRun.STATUS_FAILED, SparkAgentRun.STATUS_INTERRUPTED}:
            return Response({'error': 'Only failed or interrupted tasks can be retried.',
                             'run': AgentRunService.serialize(run)}, status=409)
        continuation = dict(run.continuation or {})
        if not continuation.get('query_suffix') and not continuation.get('kind'):
            return Response({'error': 'This task has no saved continuation to retry.'}, status=409)
        attempt_id = uuid.uuid4().hex
        with transaction.atomic():
            locked = SparkAgentRun.objects.select_for_update().get(pk=run.pk)
            if locked.status not in {SparkAgentRun.STATUS_FAILED, SparkAgentRun.STATUS_INTERRUPTED}:
                return Response({'error': 'This task changed state; refresh and try again.'},
                                status=409)
            locked.status = SparkAgentRun.STATUS_RUNNING
            locked.attempt_id = attempt_id
            locked.lease_expires_at = timezone.now() + timezone.timedelta(seconds=300)
            locked.save(update_fields=['status', 'attempt_id', 'lease_expires_at', 'updated_at'])
        agent_journal.append(run, 'run_resumed', {'attempt_id': attempt_id, 'retry': True})
        from pwanimate.tasks.agent_runs import dispatch_run
        dispatch_run(run.id, attempt_id)
        return Response({'run': AgentRunService.serialize(run), 'retried': True}, status=202)


def _error_response(message, http_status):
    return Response({'error': message}, status=http_status)


def _prepare_submission(request, data):
    """Resolve which run/interaction an answer payload targets — without
    mutating anything. Returns (run, interaction, raw_answer, via, legacy_text)
    or None when the payload carries no answer at all."""
    interaction_id = data.get('interaction_id')
    if interaction_id:
        try:
            uuid.UUID(str(interaction_id))
        except (ValueError, TypeError):
            raise InteractionError('This question is unavailable.', 'not_found')
        interaction = SparkAgentInteraction.objects.filter(
            pk=interaction_id, user=request.user).select_related('run').first()
        if interaction is None:
            raise InteractionError('This question is unavailable.', 'not_found')
        run = interaction.run
        # `answer` may be a structured dict; otherwise text/selected/message.
        raw = data.get('answer')
        if raw is None:
            raw = {'text': data.get('message'), 'selected': data.get('selected')}
        via = data.get('submitted_via') or (
            'composer' if data.get('message') is not None
            and data.get('answer') is None and data.get('selected') is None
            else 'embedded')
        return run, interaction, raw, via, ''

    # Legacy payloads: resume_run_id + clarification_answer (or a composer
    # `message` explicitly associated with the run).
    legacy_text = str(data.get('clarification_answer') or data.get('message')
                      or data.get('answer') or '').strip()
    if not legacy_text:
        return None
    run = SparkAgentRun.objects.filter(
        pk=data.get('resume_run_id'), user=request.user).first()
    if run is None:
        raise RunError('This task is unavailable.')
    pending = SparkAgentInteraction.objects.filter(
        run=run, status=SparkAgentInteraction.STATUS_PENDING,
    ).order_by('-created_at').first()
    if pending is not None:
        return run, pending, legacy_text, 'legacy', ''
    # Runs paused before interactions existed: keep the original suffix path.
    return run, None, None, 'legacy', legacy_text


def resume_run_response(view, request, activity, progress, token_callback):
    """Continuation entry point used by the non-streaming chat path.

    Returns a DRF Response for JSON clients, or a journal-tail
    StreamingHttpResponse when the request negotiated SSE.
    """
    data = request.data
    try:
        uuid.UUID(str(data.get('resume_run_id') or data.get('interaction_id')))
    except (ValueError, TypeError):
        return _error_response('This task is unavailable.', 400)

    is_stream = token_callback is not None

    # --- action approvals: PendingAction/ActionService remains the authority.
    # An explicit decision payload always wins — a stray `message` field must
    # never reroute an approval resume into the answer path.
    is_decision = bool(data.get('decision') or data.get('action_ids'))
    has_answer = (not is_decision) and bool(
        data.get('interaction_id') or data.get('clarification_answer')
        or data.get('answer') or data.get('selected')
        or (data.get('resume_run_id') and data.get('message')))
    if not has_answer:
        try:
            run, resume = AgentRunService.decide(
                request.user, data['resume_run_id'], data.get('decision'),
                data.get('action_ids'))
        except (ValueError, TypeError, RunError) as exc:
            return _error_response(str(exc), 400)
        if not resume:
            return Response({'approval_pending': True,
                             'run': AgentRunService.serialize(run)})
        continuation = {'kind': 'approval',
                        'note': 'Your decisions are recorded. Continuing the task.',
                        'via': 'embedded'}
        if is_stream:
            return _arm_and_tail(request, run, continuation)
        return _execute_sync(request, run, continuation)

    # --- clarification / structured interaction answers
    try:
        prepared = _prepare_submission(request, data)
    except InteractionError as exc:
        http = {'not_found': 404, 'resolved': 409, 'expired': 410}.get(exc.code, 400)
        return _error_response(str(exc), http)
    except RunError as exc:
        return _error_response(str(exc), 400)
    if prepared is None:
        return _error_response('This task is unavailable.', 400)
    run, interaction, raw_answer, via, legacy_text = prepared

    if run.action_ids:
        return _error_response('This task is waiting for action approval, not an answer.', 400)

    continuation = {'kind': 'clarification',
                    'note': 'Your answer is recorded. Continuing the task.',
                    'via': via}
    if is_stream:
        return _claim_and_tail(request, run, continuation, interaction=interaction,
                               raw_answer=raw_answer, legacy_text=legacy_text, via=via)
    return _claim_and_execute(request, run, continuation, interaction=interaction,
                              raw_answer=raw_answer, legacy_text=legacy_text, via=via)


def _claim_and_tail(request, run, continuation, *, interaction, raw_answer,
                    legacy_text, via):
    """SSE path: atomic claim + durable dispatch, then tail the journal."""
    try:
        if run.status == SparkAgentRun.STATUS_WAITING:
            locked, attempt_id = agent_execution.claim_run(
                request.user, run, interaction=interaction, raw_answer=raw_answer,
                legacy_text=legacy_text, note=continuation['note'],
                kind=continuation['kind'], via=via)
            from pwanimate.tasks.agent_runs import dispatch_run
            dispatch_run(locked.id, attempt_id)
        elif run.status == SparkAgentRun.STATUS_RUNNING and run.attempt_id:
            locked = _arm_attempt(run, continuation)
        else:
            raise RunError('This task has already continued. Refresh the conversation to see its status.')
    except InteractionError as exc:
        http = {'not_found': 404, 'resolved': 409, 'expired': 410}.get(exc.code, 400)
        return _error_response(str(exc), http)
    except (RunError, TransitionError) as exc:
        msg = str(exc)
        http = (409 if 'already continued' in msg
                else 410 if 'expired' in msg else 400)
        return _error_response(msg, http)
    return _stream_run_events(request.user, locked.id)


def _claim_and_execute(request, run, continuation, *, interaction, raw_answer,
                       legacy_text, via):
    """JSON path: atomic claim then synchronous execution."""
    try:
        if run.status == SparkAgentRun.STATUS_WAITING:
            locked, _ = agent_execution.claim_run(
                request.user, run, interaction=interaction, raw_answer=raw_answer,
                legacy_text=legacy_text, note=continuation['note'],
                kind=continuation['kind'], via=via)
        else:
            # Already running means another attempt owns this run.
            raise RunError('This task has already continued. Refresh the conversation to see its status.')
        payload = agent_execution.execute_claimed(locked)
    except InteractionError as exc:
        http = {'not_found': 404, 'resolved': 409, 'expired': 410}.get(exc.code, 400)
        return _error_response(str(exc), http)
    except RunError as exc:
        msg = str(exc)
        http = (409 if 'already continued' in msg
                else 410 if 'expired' in msg else 503)
        return _error_response(msg, http)
    except Exception:
        logger.exception('Spark run continuation failed run=%s', run.id)
        try:
            agent_execution.mark_failed(run, None)
        except Exception:
            pass
        return Response({'error': 'The task could not continue. Approved actions remain recorded; refresh to inspect their outcomes.'},
                        status=503)
    if payload.get('cancelled'):
        return Response({'cancelled': True, 'run_id': str(run.id),
                         'message': 'Stopped — you cancelled this task.'})
    return Response(payload)


def _arm_attempt(run, continuation):
    """Approval path: decide() already moved the run to running; arm a fresh
    attempt + lease and store the continuation, then let the caller dispatch."""
    attempt_id = uuid.uuid4().hex
    with transaction.atomic():
        locked = SparkAgentRun.objects.select_for_update().get(pk=run.pk)
        if locked.status != SparkAgentRun.STATUS_RUNNING:
            raise RunError('This task has already continued. Refresh the conversation to see its status.')
        locked.attempt_id = attempt_id
        locked.lease_expires_at = timezone.now() + timezone.timedelta(seconds=300)
        locked.continuation = continuation
        locked.save(update_fields=['attempt_id', 'lease_expires_at', 'continuation', 'updated_at'])
    agent_journal.append(locked, 'run_resumed', {'attempt_id': attempt_id,
                                                 'kind': continuation.get('kind')})
    from pwanimate.tasks.agent_runs import dispatch_run
    dispatch_run(locked.id, attempt_id)
    return locked


def _arm_and_tail(request, run, continuation):
    try:
        locked = _arm_attempt(run, continuation)
    except (RunError, TransitionError) as exc:
        return _error_response(str(exc), 400)
    return _stream_run_events(request.user, locked.id)


def _execute_sync(request, run, continuation):
    """JSON approval path: decide() already claimed; execute inline."""
    try:
        locked = run
        locked.continuation = continuation
        locked.save(update_fields=['continuation', 'updated_at'])
        payload = agent_execution.execute_claimed(locked)
    except RunError as exc:
        msg = str(exc)
        http = (409 if 'already continued' in msg
                else 410 if 'expired' in msg else 503)
        return _error_response(msg, http)
    except Exception:
        logger.exception('Spark run continuation failed run=%s', run.id)
        try:
            agent_execution.mark_failed(run, None)
        except Exception:
            pass
        return Response({'error': 'The task could not continue. Approved actions remain recorded; refresh to inspect their outcomes.'},
                        status=503)
    if payload.get('cancelled'):
        return Response({'cancelled': True, 'run_id': str(run.id),
                         'message': 'Stopped — you cancelled this task.'})
    return Response(payload)


def _stream_run_events(user, run_id, after_seq=0):
    """Journal-tail SSE: replays missed events, then live-polls until the run
    pauses again or reaches a terminal state.

    Works across processes because the journal lives in Postgres — no
    in-process queue is required. Token deltas are not journaled (bounded
    strategy); clients receive activity events live and the final answer at
    completion.
    """
    async def stream():
        seq = int(after_seq or 0)
        deadline = time.monotonic() + TAIL_MAX_SECONDS
        last_keepalive = time.monotonic()
        yield 'event: status\ndata: {"stage":"thinking"}\n\n'
        while time.monotonic() < deadline:
            run, events = await asyncio.to_thread(
                agent_journal.events_after, user, run_id, seq, 200)
            if run is None:
                yield _sse_response(404, {'error': 'This task is unavailable.'})
                return
            for entry in events:
                seq = entry.seq
                yield _journal_event_sse(entry)
            last_keepalive = time.monotonic()
            if _run_settled(run):
                payload = await asyncio.to_thread(_terminal_payload, run)
                yield _sse_response(200, payload)
                return
            await asyncio.sleep(TAIL_POLL_SECONDS)
            if time.monotonic() - last_keepalive >= TAIL_KEEPALIVE_SECONDS:
                yield ': keep-alive\n\n'
                last_keepalive = time.monotonic()
        yield _sse_response(504, {'error': 'The task is still running. Refresh to see its latest state.'})

    response = StreamingHttpResponse(stream(), content_type='text/event-stream')
    response['Cache-Control'] = 'no-cache, no-transform'
    response['X-Accel-Buffering'] = 'no'
    response['X-Content-Type-Options'] = 'nosniff'
    return response


def _run_settled(run):
    """Terminal, or paused again with a persisted result (re-asked question)."""
    if AgentRunService.is_terminal(run):
        return True
    return (run.status == SparkAgentRun.STATUS_WAITING
            and run.result_message_id is not None)


def _sse_response(status_code, data):
    encoded = JSONRenderer().render({'status_code': status_code, 'data': data}).decode('utf-8')
    return f'event: response\ndata: {encoded}\n\n'


def _journal_event_sse(entry):
    payload = entry.payload or {}
    if entry.type == 'activity' and payload.get('type') == 'activity':
        return f"event: activity\ndata: {json.dumps(payload, separators=(',', ':'))}\n\n"
    # Lifecycle journal entries surface as plan notes in the trail.
    notes = {
        'run_resumed': 'Continuing your task.',
        'run_cancelled': 'This task was cancelled.',
        'run_failed': 'This task could not continue.',
        'run_interrupted': 'Execution was interrupted. You can retry it.',
        'run_expired': 'This task expired.',
        'cancel_requested': 'Cancellation requested.',
        'clarification_answered': 'Answer recorded.',
    }
    note = {'type': 'activity', 'id': f'journal-{entry.seq}', 'kind': 'plan',
            'text': notes.get(entry.type, 'Task update.'), 'status': 'completed',
            'at': entry.created_at.isoformat() if entry.created_at else ''}
    return f"event: activity\ndata: {json.dumps(note, separators=(',', ':'))}\n\n"


def _terminal_payload(run):
    """Final response envelope built from durable state, not request memory."""
    base = {'run_id': str(run.id), 'status': run.status,
            'activity': run.activity or []}
    if run.status == SparkAgentRun.STATUS_CANCELLED:
        base.update({'cancelled': True,
                     'answer': 'Stopped — you cancelled this task.',
                     'message': 'Stopped — you cancelled this task.'})
        return base
    if run.status == SparkAgentRun.STATUS_FAILED:
        base['error'] = ('The task could not continue. Approved actions remain '
                         'recorded; refresh to inspect their outcomes.')
        return base
    if run.status == SparkAgentRun.STATUS_EXPIRED:
        base['error'] = 'This task expired. Ask Spark again to get a fresh answer.'
        return base
    if run.result_message_id:
        msg = PwanimateMessage.objects.filter(pk=run.result_message_id).first()
        if msg is not None:
            base.update({
                'answer': msg.content, 'message_id': msg.id,
                'conversation_id': str(msg.conversation_id),
                'citations': msg.citations or [], 'sources': msg.sources or [],
                'provider': msg.provider, 'model': msg.model_id,
                'metadata': {'awaiting_clarification': False,
                             'awaiting_approval': False},
            })
            return base
    # Re-paused: surface the awaiting metadata so the UI re-renders controls.
    payload = AgentRunService.serialize(run)
    base['metadata'] = {
        'awaiting_clarification': run.wait_reason == SparkAgentRun.WAIT_CLARIFICATION,
        'awaiting_approval': run.wait_reason == SparkAgentRun.WAIT_APPROVAL,
        'run': payload,
    }
    interaction = payload.get('interaction')
    if interaction:
        base['interaction'] = interaction
    return base
