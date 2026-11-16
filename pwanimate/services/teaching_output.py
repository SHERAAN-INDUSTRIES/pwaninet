"""Public instructional output backed by an owned persisted teaching receipt."""
from django.db.models import Q
from pwanimate.models import SparkTeachingTurn

PUBLIC_TEACHING_FIELDS = {
    'session_id','state_id','phase','mode','objective','version','concept_id',
    'hint_level','difficulty','verified_concept_state','recorded_misconceptions',
    'activity_is_mastery','prerequisites',
}


def persisted_teaching(message, run=None):
    if message.role != 'assistant':
        return None
    turns = SparkTeachingTurn.objects.filter(
        state__session__user_id=message.conversation.user_id).filter(
        Q(state__session__conversation_id=message.conversation_id) |
        Q(state__session__academic_conversation_id=message.conversation_id))
    identity = (run.state or {}).get('result_teaching') if run else None
    if identity and identity.get('message_id') == message.pk:
        turns = turns.filter(pk=identity.get('turn_id'), run=run)
    else:
        turns = turns.filter(completed_message=message)
    turn = turns.first()
    if (not turn or turn.metrics.get('answer_complete') is not True
            or turn.response_markdown != message.content):
        return None
    data = turn.payload.get('teaching') or {}
    return {key: data[key] for key in PUBLIC_TEACHING_FIELDS if key in data}
