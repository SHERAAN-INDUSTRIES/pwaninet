"""Service for creating, resolving, and expiring contextual follow-up actions.

Follow-ups are structured offers attached to assistant messages that can be
accepted via click or short natural replies (yes, continue, etc.). They are
distinct from SparkAgentInteraction (which pauses execution) and persist
across reloads and stream reconnection.
"""
import re
from datetime import timedelta
from django.db import transaction
from django.utils import timezone
from pwanimate.models import (
    PwanimateMessage, PwanimateConversation, SparkAgentRun, SparkFollowUp,
    SparkTeachingState, PwanimateStudySession, SparkUnitConcept,
)
from pwanimate.followup_models import ACTION_TYPES
from pwanimate.services.teaching import TeachingService

# Affirmative short replies that resolve a single pending follow-up
AFFIRMATIVE_PATTERNS = re.compile(
    r'^(yes|sure|continue|go ahead|ok|okay|do it|let\'s do it|sounds good|sounds great)$',
    re.IGNORECASE
)
# Negative short replies that reject or cancel a pending follow-up
NEGATIVE_PATTERNS = re.compile(
    r'^(no|no thanks|nope|not now|maybe later|cancel|never mind|forget it)$',
    re.IGNORECASE
)
# Ambiguous short replies that should pass through as normal chat
AMBIGUOUS_PATTERNS = re.compile(
    r'^(what|how|why|can you|could you|would you|tell me|explain|show me|help me|please)',
    re.IGNORECASE
)


def create_follow_ups(message, suggestions=None, run=None):
    """Create follow-up actions for an assistant message.

    Args:
        message: The PwanimateMessage (assistant role) to attach to.
        suggestions: List of dicts, each with 'action_type', 'label', and 'payload'.
        run: Optional SparkAgentRun to associate with (e.g. teaching runs).

    Returns:
        List of created SparkFollowUp instances (max 3).
    """
    if message.role != 'assistant':
        return []
    if not suggestions or not isinstance(suggestions, list):
        return []

    # Cap at 3 suggestions per message
    suggestions = suggestions[:3]
    now = timezone.now()
    created = []

    with transaction.atomic():
        # Expire all previous follow-ups for this conversation when a new
        # assistant message arrives (superseding old offers)
        SparkFollowUp.objects.filter(
            conversation=message.conversation,
            status='pending'
        ).update(status='expired', resolved_at=now)

        for spec in suggestions:
            if not isinstance(spec, dict):
                continue
            action_type = spec.get('action_type') or ''
            label = str(spec.get('label') or '')[:120]
            payload = spec.get('payload') or {}

            # Validate action_type
            valid_types = [choice[0] for choice in ACTION_TYPES]
            if action_type not in valid_types:
                continue

            # Validate label
            if not label.strip():
                continue

            # Validate payload structure (basic key validation)
            if not isinstance(payload, dict):
                payload = {}

            # Teaching-specific validation: require session/unit in payload for teaching actions
            if action_type in ('teaching_continue', 'teaching_topic'):
                if 'session_id' not in payload and 'conversation_id' not in payload:
                    continue

            # Create follow-up with 24-hour expiration
            follow_up = SparkFollowUp.objects.create(
                message=message,
                conversation=message.conversation,
                user=message.conversation.user,
                run=run,
                action_type=action_type,
                label=label,
                payload=payload,
                status='pending',
                expires_at=now + timedelta(hours=24),
            )
            created.append(follow_up)

    return created


class FollowUpError(ValueError):
    def __init__(self, message, code=400):
        super().__init__(message)
        self.code = code


def resolve_follow_up(follow_up_id, user, *, consumed_via='click'):
    # Resolution alone never authorizes or consumes an action.
    item = SparkFollowUp.objects.filter(pk=follow_up_id, user=user).first()
    if item is None:
        raise FollowUpError('Follow-up not found.', 404)
    return item


def expire_conversation_followups(conversation_id):
    SparkFollowUp.objects.filter(conversation_id=conversation_id, status='pending').update(
        status='expired', resolved_at=timezone.now())


def get_pending_follow_ups(conversation, user):
    return SparkFollowUp.objects.filter(conversation=conversation, user=user,
        status='pending', expires_at__gt=timezone.now()).order_by('created_at')


def resolve_short_reply(short_reply, conversation, user):
    text = short_reply.strip()
    if len(text) > 20:
        return None
    pending = list(get_pending_follow_ups(conversation, user)[:2])
    if len(pending) != 1:
        return None
    item = pending[0]
    if NEGATIVE_PATTERNS.fullmatch(text) or text.casefold() == 'skip':
        with transaction.atomic():
            item = SparkFollowUp.objects.select_for_update().get(pk=item.pk, user=user)
            if item.status != 'pending':
                return None
            item.status = 'cancelled'
            item.resolved_at = timezone.now()
            item.save(update_fields=['status', 'resolved_at'])
        return item
    return item if AFFIRMATIVE_PATTERNS.fullmatch(text) else None


def _context(item, user):
    from pwanimate.services.concept_mastery import ConceptMasteryService
    item.clean()
    payload = item.payload
    allowed = {'session_id', 'concept_id', 'conversation_id', 'question', 'interaction_id'}
    if not isinstance(payload, dict) or set(payload) - allowed:
        raise FollowUpError('Unsupported follow-up arguments.')
    if payload.get('conversation_id') and str(item.conversation_id) != str(payload['conversation_id']):
        raise FollowUpError('Conversation mismatch.')
    if not item.message_id:
        raise FollowUpError('Originating answer is unavailable.')
    session = None
    if item.action_type not in dict(ACTION_TYPES):
        raise FollowUpError('Unsupported follow-up action.')
    if item.action_type in {'teaching_continue', 'teaching_topic', 'practice_problem'}:
        from django.db.models import Q
        session = PwanimateStudySession.objects.filter(pk=payload.get('session_id'), user=user, status='active').filter(Q(conversation=item.conversation) | Q(academic_conversation_id=item.conversation_id)).first()
        if session is None:
            raise FollowUpError('Active originating study session required.')
        from pwanimate.services.study_context import resolve
        runtime_session = resolve(user,item.conversation).session
        if runtime_session is None or runtime_session.pk != session.pk:
            raise FollowUpError('Originating session is no longer the runtime session.', 409)
        with transaction.atomic():
            TeachingService._authorize(session, user)
    concept = None
    if payload.get('concept_id'):
        concept = ConceptMasteryService.owned_concept(user, payload['concept_id'])
        if session is None or concept.record_id != session.learning_record_id:
            raise FollowUpError('Concept does not belong to this unit and semester.')
    if item.action_type == 'teaching_topic' and concept is None:
        raise FollowUpError('A specific owned topic is required.')
    commands = {'teaching_continue': 'continue', 'practice_problem': 'practice',
        'clarification': 'clarify'}
    if item.action_type == 'answer_followup':
        question = payload.get('question')
        if not isinstance(question, str) or not question.strip() or len(question) > 1000:
            raise FollowUpError('A bounded follow-up question is required.')
        query = question.strip()
    elif item.action_type == 'teaching_topic':
        query = 'Teach me ' + concept.title
    else:
        query = commands[item.action_type]
    return session, concept, query


def execute_follow_up_action(follow_up, user):
    """Claim briefly; execute through Spark outside locks; persist replay result.

    Interrupted attempts are fenced, never automatically replayed. A known
    failed generation can retry the same saved user message/teaching receipt.
    """
    from pwanimate.services import agent_execution
    from pwanimate.services.conversation import ConversationService
    from pwanimate.models import SparkAgentInteraction
    from pwanimate.services.agent_runs import RUN_LEASE_SECONDS
    from django.core.exceptions import ValidationError
    error = None
    with transaction.atomic():
        from pwanimate.models import PwanimateConversation
        conversation_id=SparkFollowUp.objects.filter(pk=follow_up.pk,user=user).values_list('conversation_id',flat=True).first()
        if conversation_id is None:raise FollowUpError('Follow-up not found.',404)
        PwanimateConversation.objects.select_for_update().get(pk=conversation_id,user=user)
        item = SparkFollowUp.objects.select_for_update().filter(pk=follow_up.pk, user=user).first()
        if item is None:
            raise FollowUpError('Follow-up not found.', 404)
        if item.status == 'consumed':
            return item.execution
        if item.execution_run_id and item.execution_run.status in {'interrupted','cancelled'}:
            item.status = 'interrupted'
            item.save(update_fields=['status'])
            return {'executed': False, 'status': 'interrupted', 'follow_up_id': str(item.pk)}
        if item.execution_run_id and item.execution_run.status != 'failed' and item.execution_run.result_message_id:
            message = item.execution_run.result_message
            result = {'executed': True, 'status': 'consumed', 'follow_up_id': str(item.pk),
                'message_id': message.pk, 'answer': message.content,
                'conversation_id': str(item.conversation_id)}
            item.status, item.execution = 'consumed', result
            item.save(update_fields=['status', 'execution'])
            return result
        if item.status == 'executing':
            if item.claimed_at is None or item.claimed_at < timezone.now() - timedelta(seconds=RUN_LEASE_SECONDS):
                item.status = 'interrupted'
                item.save(update_fields=['status'])
            return {'executed': False, 'status': item.status, 'follow_up_id': str(item.pk)}
        if item.status in {'cancelled', 'expired', 'interrupted'}:
            error = FollowUpError('Follow-up is ' + item.status, 410 if item.status != 'interrupted' else 409)
        elif item.expires_at <= timezone.now():
            item.status = 'expired'
            item.resolved_at = timezone.now()
            item.save(update_fields=['status', 'resolved_at'])
            error = FollowUpError('Follow-up expired.', 410)
        if error is None:
            try:
                session, concept, query = _context(item, user)
            except (ValueError, LookupError, ValidationError) as exc:
                error = FollowUpError(str(exc))
        if error is None:
            # Never steal a waiting interaction. Only the explicitly bound one
            # may be resumed; otherwise a suggestion represents a new user turn.
            interaction = None
            if item.payload.get('interaction_id') and not item.execution_run_id:
                interaction = SparkAgentInteraction.objects.filter(pk=item.payload['interaction_id'],
                    run=item.run, user=user, status='pending').first()
                if interaction is None or item.run.status != 'waiting':
                    raise FollowUpError('Originating interaction is no longer waiting.', 409)
                if session and TeachingService.state_for(session, user).active_run_id != item.run_id:
                    raise FollowUpError('Teaching run mismatch.', 409)
                run, _ = agent_execution.claim_run(user, item.run, interaction=interaction,
                    raw_answer=({'selected': [query]} if any(o.get('id') == query for o in interaction.options) else {'text': query}), via='follow_up')
                item.execution_run = run
            elif not item.execution_run_id:
                if SparkAgentRun.objects.filter(user=user, user_message__conversation=item.conversation,
                        status__in=['waiting', 'running', 'queued']).exists():
                    raise FollowUpError('Finish the current Spark task first.', 409)
                message = ConversationService.persist_user_message(item.conversation, query)
                item.execution_run = SparkAgentRun.objects.create(user=user, user_message=message,
                    status='running', snapshot={'query': query, 'user_message_id': message.pk,
                        'history': list(item.conversation.messages.filter(id__lte=item.message_id).order_by('-id').values('role', 'content')[:10])[::-1]},
                    state={'teaching_state_id': str(TeachingService.state_for(session, user).pk)} if session else {},
                    continuation={'followup_action': item.action_type},
                    expires_at=item.expires_at)
            if item.execution_run_id and item.status == 'failed':
                SparkAgentRun.objects.filter(pk=item.execution_run_id).update(status='running')
                item.execution_run.refresh_from_db()
            item.status = 'executing'
            item.claimed_at = timezone.now()
            item.save(update_fields=['status', 'claimed_at', 'execution_run'])
    if error:
        raise error
    try:
        # Fresh authorization immediately before work, including retries.
        session, concept, query = _context(item, user)
        if concept and item.action_type == 'teaching_topic':
            state = TeachingService.state_for(session, user)
            TeachingService.configure(session, user, mode=state.mode, concept_id=concept.pk)
        result = agent_execution.execute_claimed(item.execution_run)
        item.execution_run.refresh_from_db()
        if item.execution_run.status == 'failed':
            raise RuntimeError('Continuation failed; inspect saved outcomes before retrying.')
        if item.execution_run.status in {'interrupted','cancelled'}:
            SparkFollowUp.objects.filter(pk=item.pk, status='executing').update(status='interrupted')
            return {'executed':False, 'status':'interrupted', 'follow_up_id':str(item.pk),
                    'message_id':item.execution_run.result_message_id}
        if not result.get('message_id'):
            raise RuntimeError('Continuation did not persist an answer.')
        result.update(executed=True, status='consumed', follow_up_id=str(item.pk))
        SparkFollowUp.objects.filter(pk=item.pk, status='executing').update(
            status='consumed', execution=result, resolved_at=timezone.now())
        return result
    except (ValueError, LookupError) as exc:
        SparkFollowUp.objects.filter(pk=item.pk, status='executing').update(status='failed')
        raise FollowUpError(str(exc)) from exc
    except Exception:
        SparkFollowUp.objects.filter(pk=item.pk, status='executing').update(status='failed')
        raise
