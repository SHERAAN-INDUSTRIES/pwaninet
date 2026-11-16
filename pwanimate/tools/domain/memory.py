"""Optional, non-blocking proposals for allowlisted response preferences only."""
import re
from datetime import timedelta
from django.conf import settings
from django.db import transaction
from django.utils import timezone
from pwanimate.models import PwanimateMessage, SparkPersonalMemory, SparkMemoryPreference
from pwanimate.services.personal_memory import PersonalMemoryService, enabled, payload
from pwanimate.tools.base import BaseDomainTool, ToolResult

PREFERENCES = {
    'concise': 'Preferred response length: concise.',
    'detailed': 'Preferred response length: detailed.',
    'bullet points': 'Preferred response format: bullet points.',
    'paragraphs': 'Preferred response format: paragraphs.',
    'English': 'Preferred response language: English.',
    'Kiswahili': 'Preferred response language: Kiswahili.',
}


def stated_preference(text, value):
    # Recognize whole, explicit preference requests, including combined choices.
    # Every clause must be allowlisted; reject embedded documents/personal facts.
    if not isinstance(text, str) or value not in PREFERENCES: return False
    text = re.sub(r'^\s*Spark[,:]\s*', '', text, flags=re.I)
    if not re.match(r'^(?:I prefer|I would prefer|please\s+(?:use|keep)|use|keep)\s+', text.strip(), re.I): return False
    clauses = re.split(r'\s+and\s+', text.strip().rstrip('.!'), flags=re.I)
    found = set()
    for clause in clauses:
        clause = re.sub(r'^(?:please\s+)?(?:I prefer|I would prefer|use|keep)\s+', '', clause.strip(), flags=re.I)
        clause = re.sub(r'^please\s+(?:use|keep)\s+', '', clause, flags=re.I)
        matches = []
        for preference in PREFERENCES:
            pattern = re.escape(preference) + r'(?:\s+(?:answers|responses|explanations))?'
            if preference == 'concise':
                pattern = r'(?:' + pattern + r'|short(?:\s+(?:answers|responses|explanations))?|(?:answers|responses|explanations)\s+short)'
            if re.fullmatch(pattern, clause, re.I): matches.append(preference)
        if len(matches) != 1: return False
        found.add(matches[0])
    if len(found) > 2 or {'concise','detailed'} <= found or {'bullet points','paragraphs'} <= found or {'English','Kiswahili'} <= found:
        return False
    return value in found


class ProposePersonalMemoryTool(BaseDomainTool):
    name = 'propose_personal_memory'
    description = ('Optionally propose a response preference ONLY when the latest user message explicitly states it. '
        'Never save it as approved. The student reviews the exact fact in chat; normal chat continues. '
        'Do not propose sensitive personal information or infer preferences from study material.')
    parameters_schema = {'type':'object','properties':{'preference':{'type':'string','enum':list(PREFERENCES)}},
                         'required':['preference'],'additionalProperties':False}

    def execute(self,user,preference,source_message_id=None,**kwargs):
        if not getattr(settings,'SPARK_MEMORY_PROPOSALS_ENABLED',True): return ToolResult.ok({'proposed':False,'status':'feature_disabled'})
        if not user or not user.is_authenticated or preference not in PREFERENCES: return ToolResult.fail('Memory proposal unavailable')
        source=PwanimateMessage.objects.filter(pk=source_message_id,role='user',conversation__user=user).first()
        if source is None or not stated_preference(source.content,preference): return ToolResult.ok({'proposed':False,'status':'ineligible'})
        fact_text = PREFERENCES[preference]
        PersonalMemoryService.preference(user)
        with transaction.atomic():
            pref=SparkMemoryPreference.objects.select_for_update().get(user=user)
            if not enabled(pref): return ToolResult.ok({'proposed':False,'status':'memory_disabled'})
            # Do not resurrect a rejected proposal on a retry of the same turn.
            if SparkPersonalMemory.objects.filter(user=user,source_message_id=source.pk,status='rejected').exists():
                return ToolResult.ok({'proposed':False,'status':'rejected'})
            existing=SparkPersonalMemory.objects.filter(user=user,text=fact_text,status__in=['proposed','approved']).first()
            if existing:
                return ToolResult.ok({'proposed':False,'status':'already_pending' if existing.status=='proposed' else 'already_approved'})
            recent=SparkPersonalMemory.objects.filter(user=user,created_at__gte=timezone.now()-timedelta(days=1))
            # One explicit source request per day, up to two independently
            # approved facts from that same request (e.g. length + format).
            if recent.exclude(source_message_id=source.pk).exists() or recent.count() >= 2:
                return ToolResult.ok({'proposed':False,'status':'rate_limited'})
            fact=PersonalMemoryService.propose(user,fact_text,source.pk)
            return ToolResult.ok({'proposed':True,'status':'proposed','memory_proposal':payload(fact),
                'notice':'Proposed only; explicit student approval is required.'})


def propose_from_message(user, message):
    """Optional deterministic entry point for explicit preferences, independent
    of planner/tool selection. The tool rechecks the persisted source, owner,
    feature flag, consent and frequency under the same WP3 lock.
    """
    if not user or not user.is_authenticated or message.conversation.user_id != user.pk or message.role != 'user':
        return {'status':'ineligible'}
    preferences = [key for key in PREFERENCES if stated_preference(message.content,key)]
    if not preferences: return {'status':'ineligible'}
    statuses = []
    for preference in preferences:
        result = ProposePersonalMemoryTool().execute(user,preference,source_message_id=message.pk)
        statuses.append(result.data.get('status','unavailable') if result.success else 'unavailable')
    return {'status':'proposed' if 'proposed' in statuses else statuses[0]}


class RecallMemoryTool(BaseDomainTool):
    name = 'recall_memory'
    description = (
        'Read this student\'s approved saved facts and authorized academic history across conversations. '
        'Use for what we studied/devoured last time or a remembered topic, including before planning a revision. '
        'Missing records are not proof nothing happened. Does not search raw transcripts, resume sessions or authorize writes.')
    parameters_schema = {'type': 'object', 'properties': {
        'query': {'type': 'string', 'minLength': 1, 'maxLength': 2000},
        'limit': {'type': 'integer', 'minimum': 1, 'maximum': 12}}, 'required': ['query']}

    def execute(self, user, query, limit=6, **kwargs):
        if not user or not getattr(user, 'is_authenticated', False):
            return ToolResult.fail('Sign in to read saved history.', error_type='permission_denied')
        import json
        from pwanimate.services.unit_learning import UnitLearningService
        from pwanimate.context.academic_history import bounded_academic_json
        facts = PersonalMemoryService.recall(user, query, limit=limit)
        academic = json.loads(bounded_academic_json(UnitLearningService.safe_context(user, query)))
        return ToolResult.ok({'facts': facts, 'academic_history': academic,
            'read_only': True, 'grants_write_authorization': False,
            'status': 'records_found' if facts or academic else 'no_accessible_saved_records',
            'source_scope': 'approved facts and persisted academic records; no raw transcript recall',
            'evidence_semantics': 'Saved self-reports and teaching activity do not establish independent mastery.'})
