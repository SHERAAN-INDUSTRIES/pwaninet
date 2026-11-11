"""Public execution events. Never expose raw model reasoning or tool argument dumps."""
import uuid
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
}
STAGES = {'thinking':'Looking at your request', 'searching_web':'Searching the web',
 'reading_sources':'Reading the sources', 'devouring_context':'Reading the selected material',
 'searching_posts':'Searching campus posts', 'searching_documents':'Searching document content',
 'writing_answer':'Putting the answer together'}

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
    'writing_answer': 'response_draft',
}


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
    return {'type':'activity', 'id':event_id or str(uuid.uuid4()), 'kind':kind,
            'text':str(text)[:400], 'status':status, 'activity_type':activity_type,
            'at':datetime.now(timezone.utc).isoformat(), **extra}


def emit(callback, kind, text, **kwargs):
    value = event(kind, text, **kwargs)
    if callback:
        callback(value)
    return value
