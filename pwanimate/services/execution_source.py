"""Owned source provenance for a durable execution, independent of run identity."""
from pwanimate.models import PwanimateMessage
from pwanimate.services.agent_runs import RunError


def effective_execution_source(run):
    answer_id = (run.continuation or {}).get('answer_message_id')
    boundary_id = (run.snapshot or {}).get('execution_boundary_id')
    source_id = answer_id or boundary_id or run.user_message_id
    source = PwanimateMessage.objects.filter(
        pk=source_id, role='user', conversation_id=run.user_message.conversation_id,
        conversation__user_id=run.user_id).first()
    if source is None:
        raise RunError('The execution source is unavailable or belongs to another conversation.')
    if not answer_id and boundary_id and source.task != 'academic_execution':
        raise RunError('The academic execution receipt is invalid.')
    return source
