"""Public execution events. Never expose raw model reasoning or tool argument dumps."""
import uuid
import json
from datetime import datetime, timezone

LABELS = {
 'academic_lookup':'Looking up academic information', 'student_timetable':'Checking your class timetable',
 'document_detail':'Opening document information', 'retrieve_documents':'Searching document content',
 'group_announcements':'Checking group updates', 'user_profile':'Reading the available profile',
 'notification_summary':'Checking your notifications', 'people_discovery':'Finding suitable study peers',
 'local_time':'Checking the current time', 'content_search':'Searching campus content',
 'web_search':'Searching the web', 'site_info':'Reading official platform information',
 'personal_schedule':'Checking your plans and reminders', 'create_study_plan':'Saving your study plan',
 'create_reminder':'Saving your reminder', 'unread_chats':'Checking unread chat counts',
 'send_message':'Preparing a message for your approval', 'follow_user':'Preparing a follow request',
 'send_notification':'Creating your notification',
 'propose_personal_memory':'Preparing a preference for your review',
 'recall_memory':'Reviewing your saved study history',
 'add_study_block':'Adding your study session', 'update_study_block':'Updating your study session',
 'remove_study_block':'Removing your study session', 'complete_study_block':'Recording your completed session',
}
STAGES = {'thinking':'Looking at your request', 'searching_web':'Searching the web',
 'reading_sources':'Reading the sources', 'devouring_context':'Reading the selected material',
 'searching_posts':'Searching campus posts', 'searching_documents':'Searching document content',
 'teaching_check':'Preparing your lesson', 'writing_answer':'Putting the answer together',
 'reading_document_page':"Checking the page you're reading"}

# Canonical semantic activity types. The frontend resolves its Lucide icon
# from `activity_type`; `status` carries the outcome separately, so an
# operation keeps its identity whether it is running, completed or failed.
# Events persisted before this field existed fall back to the deterministic
# kind/tool/stage derivation below — the same mapping, applied client-side.
_KIND_ACTIVITY_TYPES = {
    'thinking': 'thinking',
    'plan': 'thinking',
    'say': 'thinking',
    'checklist': 'checklist',
    'clarification': 'clarification',
    'checklist_review': 'plan_revision',
    'verify': 'verification',
}
_TOOL_ACTIVITY_TYPES = {
    'web_search': 'web_search',
    'retrieve_documents': 'document_read',
    'academic_lookup': 'document_read',
    'document_detail': 'document_read',
    'recall_memory': 'document_read',
    'student_timetable': 'document_read',
    'content_search': 'content_search',
}
_STAGE_ACTIVITY_TYPES = {
    'thinking': 'thinking',
    'searching_web': 'web_search',
    'reading_sources': 'document_read',
    'devouring_context': 'document_read',
    'searching_documents': 'document_read',
    'searching_posts': 'content_search',
    'teaching_check': 'thinking',
    'writing_answer': 'response_draft',
    'reading_document_page': 'document_read',
}


# Explanations describe observable work, not the model's private deliberation.
# Rebuild them on BOTH serialization and replay; never trust a supplied summary.
TOOL_PURPOSES = {
    'web_search': 'I’m checking external sources for information that can support your answer.',
    'retrieve_documents': 'I’m looking for relevant passages in documents you can access, so the explanation can be grounded in the material.',
    'content_search': 'I’m checking campus content for material relevant to your request.',
    'academic_lookup': 'I’m checking academic records rather than guessing course or programme details.',
    'student_timetable': 'I’m checking your class timetable so any scheduling advice can account for your classes.',
    'document_detail': 'I’m checking the document’s available information before using it as a source.',
    'personal_schedule': 'I’m checking your existing plans and reminders before suggesting changes.',
    'send_message': 'I’m preparing a draft for you to review before a message can be sent.',
    'follow_user': 'I’m preparing a follow request for your approval before it can be applied.',
    'propose_personal_memory': 'I’m preparing the stated preference for your review; it is not approved memory yet.',
    'recall_memory': 'I’m checking your approved memories and recorded academic activity across conversations.',
    'send_notification': 'I’m creating the immediate in-app notification you requested; this does not schedule a phone alert.',
    'create_reminder': 'I’m using the requested reminder details; creating it does not establish delivery on your phone.',
    'create_study_plan': 'I’m saving the study plan you requested so it can be available later.',
    'add_study_block': 'I’m adding the study session you requested to your personal schedule.',
    'update_study_block': 'I’m applying your requested change to the existing study session.',
    'remove_study_block': 'I’m removing the study session you asked to cancel.',
    'complete_study_block': 'I’m recording the study session as completed at your request.',
}
STAGE_SUMMARIES = {
    'thinking': 'I’m identifying what your request needs and whether it calls for sources, account information, or a direct explanation.',
    'searching_web': TOOL_PURPOSES['web_search'],
    'reading_sources': 'I’m reviewing the available sources for relevance before using them in the answer.',
    'devouring_context': 'I’m reading the supplied material and connecting the relevant parts to your question.',
    'searching_posts': TOOL_PURPOSES['content_search'],
    'searching_documents': TOOL_PURPOSES['retrieve_documents'],
    'writing_answer': 'I’m putting the available evidence into a direct answer and noting any checks that could not complete.',
    'teaching_check': 'I’m checking your study session and lesson state so the next step continues from where you left off.',
    'reading_document_page': 'I’m reviewing the authorized page evidence for your document question.',
}


def _public_count(value):
    return min(value, 10000) if type(value) is int and value >= 0 else None


def execution_summary(value):
    """Only canonical event fields and bounded numeric outcomes can reach here."""
    kind, status = value.get('kind'), value.get('status')
    if kind == 'stage':
        stage = str(value.get('id', '')).removeprefix('stage-')
        return STAGE_SUMMARIES.get('thinking' if stage == 'request' else stage, '')
    if kind == 'tool':
        if status == 'failed':
            return 'This check could not complete. Its missing result cannot be treated as verified evidence.'
        if status == 'completed':
            if value.get('awaiting_review') is True:
                return 'A proposal is ready for your review. The proposed action has not been executed.'
            count = _public_count(value.get('result_count'))
            if count is not None:
                return (f'This check returned {count} result(s). I’ll assess their relevance before using them.'
                    if count else 'This check returned no matching results. It does not provide evidence for the answer.')
            return 'The tool returned a result. I’ll use the available information where it addresses your request.'
        return TOOL_PURPOSES.get(value.get('tool'), 'I’m checking the information needed for your request.')
    if kind == 'checklist':
        items = value.get('items', [])
        done = sum(item.get('status') == 'done' for item in items)
        unresolved = sum(item.get('status') in {'pending', 'blocked', 'failed'} for item in items)
        return f'{done} of {len(items)} planned step(s) completed; {unresolved} remain pending, blocked, or failed.'
    if kind in {'verify', 'checklist_review'}:
        if status == 'failed':
            return 'The final plan review did not complete. I cannot mark unresolved work as finished.'
        completed = _public_count(value.get('completed_checks'))
        failed = _public_count(value.get('failed_checks'))
        if completed is not None and failed is not None:
            return f'I’m checking the plan against {completed} successful tool check(s) and {failed} failed check(s), so unfinished work stays visible.'
        return 'I’m comparing the planned steps with the recorded outcomes, so unfinished work is not reported as complete.'
    return ''


def _derive_activity_type(kind, extra):
    """Best-effort semantic type for events that do not set one explicitly."""
    if kind == 'tool':
        return _TOOL_ACTIVITY_TYPES.get(extra.get('tool'), 'tool_call')
    if kind == 'stage':
        event_id = str(extra.get('event_id') or '')
        stage = event_id[len('stage-'):] if event_id.startswith('stage-') else ''
        return _STAGE_ACTIVITY_TYPES.get(stage)
    return _KIND_ACTIVITY_TYPES.get(kind)


def event(kind, text, *, event_id=None, status='running', activity_type=None, **extra):
    if activity_type is None:
        activity_type = _derive_activity_type(kind, {'tool': extra.get('tool'),
                                                      'event_id': event_id or ''})
    return public_event({'type':'activity', 'id':event_id or str(uuid.uuid4()), 'kind':kind,
            'text':str(text)[:400], 'status':status, 'activity_type':activity_type,
            'at':datetime.now(timezone.utc).isoformat(), **extra})


def emit(callback, kind, text, **kwargs):
    value = event(kind, text, **kwargs)
    if callback and value:
        callback(value)
    return value


# Public progress is a projection, never a dump of planner output. Apply on
# both write and read so older journals cannot reintroduce private narration.
def public_event(value):
    if not isinstance(value, dict) or value.get('type') != 'activity':
        return None
    kind = value.get('kind')
    if not isinstance(kind, str) or kind not in {'stage', 'tool', 'checklist', 'verify', 'checklist_review',
                    'clarification', 'approval', 'status'}:
        return None
    status = value.get('status')
    if not isinstance(status, str) or status not in {'running', 'completed', 'failed', 'waiting', 'answered',
                      'cancelled', 'blocked', 'retrying'}:
        status = 'running'
    clean = {k: value[k] for k in ('id', 'at', 'run_id')
             if isinstance(value.get(k), str)}
    clean.update(type='activity', kind=kind, status=status)
    if kind == 'stage':
        stage = str(value.get('id', '')).removeprefix('stage-')
        if stage == 'request': stage = 'thinking'
        if stage not in STAGES: return None
        clean.update(id='request' if value.get('id') == 'request' else 'stage-' + stage, text=STAGES[stage])
    elif kind == 'tool':
        tool = value.get('tool') if isinstance(value.get('tool'), str) else None
        clean['tool'] = tool if tool in LABELS else 'other'
        text = LABELS.get(tool, 'Checking the requested information')
        suffix = {'completed': ' — complete', 'failed': ' — could not complete'}.get(status, '')
        clean['text'] = text + suffix
    elif kind == 'checklist':
        clean.update(id='checklist', text='Execution checklist')
        items = value.get('items') if isinstance(value.get('items'), list) else []
        clean['items'] = [
            {'label': f'Step {i + 1}', 'status': item.get('status') if isinstance(item.get('status'), str) and item.get('status') in
             {'pending', 'doing', 'done', 'blocked', 'failed', 'skipped', 'cancelled'} else 'pending'}
            for i, item in enumerate(items[:8]) if isinstance(item, dict)]
        clean['unresolved'] = sum(item['status'] in {'pending','blocked','failed'} for item in clean['items'])
    elif kind in {'verify', 'checklist_review'}:
        clean.update(id='verify', text='Checking execution results')
    elif kind == 'status':
        clean['text'] = {'failed': 'This task could not complete.', 'cancelled': 'Task cancelled.',
                         'waiting': 'Waiting for your answer.'}.get(status, 'Task updated.')
    else:
        # These are deliberate user-facing review surfaces, not activity detail.
        clean['text'] = str(value.get('text') or '')[:600]
        keys = ('interaction_id', 'input_type', 'allow_custom_answer', 'questions', 'options') if kind == 'clarification' else ()
        for key in keys:
            if key in value: clean[key] = value[key]
        if kind == 'approval':
            actions = value.get('actions') if isinstance(value.get('actions'), list) else []
            clean['actions'] = [{k: action[k] for k in ('id', 'action_type', 'status', 'summary') if k in action}
                                for action in actions if isinstance(action, dict)]
    # Only carry factual outcome metadata; raw detail/summary/arguments are ignored.
    if kind == 'tool':
        count = _public_count(value.get('result_count'))
        if status == 'completed' and count is not None: clean['result_count'] = count
        if status == 'completed' and value.get('awaiting_review') is True: clean['awaiting_review'] = True
    if kind in {'verify', 'checklist_review'}:
        for key in ('completed_checks', 'failed_checks'):
            count = _public_count(value.get(key))
            if count is not None: clean[key] = count
    summary = execution_summary(clean)
    if summary: clean['summary'] = summary
    activity_type = value.get('activity_type')
    clean['activity_type'] = activity_type if isinstance(activity_type, str) and activity_type in {
        'thinking', 'checklist', 'web_search', 'document_read', 'content_search',
        'tool_call', 'plan_revision', 'verification', 'clarification', 'response_draft', 'retry'
    } else _derive_activity_type(kind, {'tool': clean.get('tool'), 'event_id': clean.get('id')})
    return clean


def public_events(values):
    result, seen = [], set()
    if not isinstance(values, (list, tuple)): return result
    for value in values or []:
        clean = public_event(value)
        if clean is None: continue
        # Ignore timestamp churn, preserve running -> terminal transitions.
        key = json.dumps({k: v for k, v in clean.items() if k != 'at'}, sort_keys=True)
        if key not in seen:
            result.append(clean)
            seen.add(key)
    return result
