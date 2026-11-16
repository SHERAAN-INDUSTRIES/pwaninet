"""SALS 0.2: enrollment-backed records, conservative attribution and explicit consent.

No curriculum synchronization, personal-memory import, mastery scoring or provider calls.
All writes serialize on the student's tracking preference, then enrollment/record.
"""
import logging
import re
from django.db import transaction
from django.utils import timezone
from documents.models import StudentUnitEnrollment
from pwanimate.models import (PendingAction, PwanimateConversation, PwanimateMessage,
    PwanimateStudySession, SparkAcademicTrackingPreference, SparkUnitLearningRecord, SparkUnitActivity)
from pwanimate.services.persistent_memory import TOPICS

logger = logging.getLogger(__name__)
HISTORY = re.compile(r'what have i (?:studied|covered|learned)|study history|my progress|what did i study|what did we study|where did we stop|mistakes.*last time|what topics have i completed|do you remember my .{0,100}(?:session|lesson)', re.I)
MEANINGFUL = re.compile(r"^(?:spark[,!:]\s*)?(?:also\s+)?(?:please\s+)?(?:explain|teach|solve|derive|practise|practice|revise|revision|review|prove|calculate|differentiate|integrate|quiz|test me|go through|study|learn|help me|can we|let's|lets|show me|walk me|give me|it'?s|this is|i'?m (?:taking|doing|studying|learning|revising|preparing)|we'?re (?:doing|studying|covering|learning)|i (?:am studying|am learning|am preparing|am revising|understand|struggle|struggling|have difficulty|have trouble|need help|need to study|need to learn|want to study|want to learn|don't understand|dont understand|don't get|dont get)|i'm studying|could you|can you|how (?:do|can) i|what (?:is|are)|questions? (?:on|about|from)|problem sets?|past papers?|homework|assignment|coursework|exam|tutorial)\b", re.I)
# A topic needs both the enrolled unit's subject title AND topic evidence. Unit
# descriptions also provide catalog-specific multiword phrases; no global keyword
# is sufficient to manufacture enrollment or to break an ambiguous tie.
SUBJECT_TOPICS = {
    'linear algebra': (*TOPICS['algebra'], 'orthogonal lines',
        'symmetric equations', 'vector spaces', 'eigenvalues',
        'systems of linear equations', 'linear systems', 'linear independence',
        'linear combination', 'vector space', 'basis', 'span', 'dimension',
        'orthogonality', 'orthogonal vectors', 'dot product', 'cross product',
        'inner product', 'scalar triple product', 'row reduction',
        'reduced row echelon', 'echelon form', 'rank', 'inverse matrix',
        'matrix inverse', 'invertible matrix', 'singular matrix',
        'linear transformation', 'eigenvector', 'eigenvalue', 'symmetric matrix',
        'skew-symmetric', 'diagonalization', 'diagonalizable', 'quadratic form',
        'equations of planes', 'plane equations', 'planes in 3d', 'plane in 3d',
        'vector equation of a plane', 'vector equations of planes', 'line in 3d',
        'lines in 3d', 'parametric equations', 'symmetric equations of lines',
        'direction vector', 'normal vector', 'point normal form',
        'scalar equation of a plane', 'cartesian equation'),
    'calculus': (*TOPICS['calculus'], 'integral', 'antiderivative',
        'definite integral', 'indefinite integral', 'fundamental theorem',
        'taylor series', 'maclaurin series', 'mean value theorem',
        'extreme values', 'optimization', 'related rates', 'area between curves',
        'volume of revolution', 'polar coordinates', 'sequences', 'series',
        'convergence', 'divergence', 'power series', 'ratio test', 'root test',
        'alternating series', 'integration by substitution'),
    'probability and statistics': ('probability', 'statistics',
        'random variable', 'expected value', 'variance', 'standard deviation',
        'probability distribution', 'conditional probability', 'bayes',
        'independent events', 'mutually exclusive', 'normal distribution',
        'sampling', 'confidence interval', 'hypothesis', 'correlation',
        'regression', 'permutation', 'combination', 'binomial', 'poisson'),
    'programming': ('java', 'recursion', 'functions', 'loops',
        'object oriented', 'oop', 'inheritance', 'polymorphism',
        'encapsulation', 'abstraction', 'class diagram', 'use case',
        'software development', 'software engineering', 'programming concepts',
        'control structures', 'data types', 'debugging', 'compilation',
        'exception handling', 'file handling'),
    'algorithms': ('recursion', 'sorting algorithms', 'graph traversal',
        'algorithm', 'data structures', 'sorting', 'searching', 'binary search',
        'time complexity', 'space complexity', 'big o', 'tree traversal',
        'dynamic programming', 'greedy algorithm', 'divide and conquer',
        'linked list', 'stack', 'queue', 'hash table', 'binary tree',
        'binary search tree', 'heap', 'priority queue'),
    'object oriented': ('object oriented', 'oop', 'use case', 'uml',
        'class diagram', 'requirement', 'system design', 'software engineering',
        'agile', 'scrum', 'inheritance', 'polymorphism', 'encapsulation'),
    'data structures': ('data structure', 'array', 'linked list', 'stack',
        'queue', 'tree', 'hash table', 'sorting', 'searching',
        'binary search', 'time complexity', 'recursion', 'algorithm'),
    'hardware': ('hardware', 'software', 'cpu', 'motherboard', 'ram',
        'hard drive', 'ssd', 'operating system', 'troubleshooting',
        'computer components', 'input devices', 'output devices',
        'storage device', 'memory module', 'bios', 'driver'),
    'physics': ('kinematics', 'thermodynamics', 'mechanics'),
}
STOP = set('the of in and to for a an this that with from is are course unit introduction advanced basic fundamentals'.split())


def normalized(text):
    return ' '.join(re.findall(r'[a-z0-9]+', str(text).casefold()))


def contains(text, phrase):
    return bool(phrase) and f' {phrase} ' in f' {text} '


class UnitLearningService:
    @staticmethod
    def preference(user):
        if not user or not user.is_authenticated:
            raise ValueError('Authentication required')
        return SparkAcademicTrackingPreference.objects.get_or_create(user=user)[0]

    @classmethod
    def set_mode(cls, user, mode):
        if not isinstance(mode, str) or mode not in {'ask', 'automatic', 'disabled'}:
            raise ValueError('mode must be ask, automatic or disabled')
        cls.preference(user)
        with transaction.atomic():
            pref = SparkAcademicTrackingPreference.objects.select_for_update().get(user=user)
            pref.mode = mode
            pref.revision += 1
            pref.changed_at = timezone.now()
            pref.save()
        return pref

    @staticmethod
    def enrollments(user):
        # Do NOT call get_enrolled_units(): it may auto-create curriculum enrollments.
        return StudentUnitEnrollment.objects.filter(user=user).select_related('user', 'academic_unit', 'semester__academic_year')

    @staticmethod
    def eligible(enrollment):
        term = enrollment.semester
        today = timezone.localdate()
        return bool(enrollment.is_active and enrollment.academic_unit.is_active and term
            and term.start_date <= today <= term.end_date
            and (not enrollment.user.semester_id or enrollment.user.semester_id == term.pk)
            and (not enrollment.academic_year_id or enrollment.academic_year_id == term.academic_year_id))

    @classmethod
    def require_writable(cls, record, user):
        if record.user_id != user.pk:
            raise LookupError('Learning record not found')
        enrollment = cls.enrollments(user).filter(pk=record.enrollment_id).first()
        if not enrollment or not cls.eligible(enrollment) or enrollment.semester_id != record.semester_id \
                or enrollment.academic_unit_id != record.academic_unit_id:
            raise ValueError('This term or enrollment is read-only')
        return enrollment

    @classmethod
    def detect(cls, user, text):
        """Return independently attributed spans, retaining ambiguity explicitly."""
        if not user or not user.is_authenticated:
            return {'matches': [], 'ambiguous': [], 'history_query': False}
        text = str(text)[:4000]
        from pwanimate.services.recall_intent import is_recent_learning_recall
        history = bool(HISTORY.search(text) or is_recent_learning_recall(text))
        continuing = bool(re.fullmatch(r'(?:continue|continue (?:our |the )?(?:previous )?lesson)[.!? ]*', text.strip(), re.I))
        if not (MEANINGFUL.search(text.strip()) or history or continuing):
            return {'matches': [], 'ambiguous': [], 'history_query': False}
        # Quoted/pasted material is not the student's own academic activity.
        if re.search(r'```|<[^>]+>|\b(?:the document says|example transcript|pretend that)\b', text, re.I):
            return {'matches': [], 'ambiguous': [], 'history_query': history}
        enrolled = [item for item in cls.enrollments(user)[:100] if cls.eligible(item)]
        matches, ambiguous = [], []
        for index, raw in enumerate(re.split(r'[;\n.!?]|\s+and\s+', text, flags=re.I)):
            raw = raw.strip()
            query = normalized(raw)
            bare_topic = index > 0 and not MEANINGFUL.search(raw) and len(query.split()) <= 8
            if not query or not (MEANINGFUL.search(raw) or history or bare_topic):
                continue
            candidates = []
            for enrollment in enrolled:
                unit = enrollment.academic_unit
                title = normalized(unit.name)
                explicit = contains(query, normalized(unit.code)) or contains(query, title)
                topics = []
                for subject, vocabulary in SUBJECT_TOPICS.items():
                    if contains(title, subject):
                        topics.extend(topic for topic in vocabulary if contains(query, topic))
                # Catalog-supported phrases, requiring >=2 meaningful words.
                words = normalized(unit.description[:4000]).split()
                for size in (5, 4, 3, 2):
                    for index in range(len(words) - size + 1):
                        phrase = ' '.join(words[index:index + size])
                        if len(set(words[index:index + size]) - STOP) >= 2 and contains(query, phrase):
                            topics.append(phrase)
                if explicit or topics:
                    if bare_topic and query not in {title, normalized(unit.code), *(normalized(t) for t in topics)}:
                        continue
                    candidates.append({'enrollment_id': enrollment.pk, 'unit_id': unit.pk,
                        'semester_id': enrollment.semester_id, 'unit_name': unit.name,
                        'topic': (max(topics, key=len) if topics else unit.name)[:255],
                        'excerpt': raw[:1000], 'explicit_unit': explicit})
            explicit = [item for item in candidates if item['explicit_unit']]
            # Multiple explicit units inside one unsplit span are not safely separable.
            certain = explicit if len(explicit) == 1 else candidates
            if len(certain) == 1:
                item = certain[0]
                if not any(m['enrollment_id'] == item['enrollment_id'] and m['topic'] == item['topic'] for m in matches):
                    matches.append(item)
            elif candidates:
                ambiguous.append({'excerpt': raw[:1000], 'candidates': candidates})
        if not matches and continuing:
            records = SparkUnitLearningRecord.objects.filter(user=user, authorized_at__isnull=False).select_related('enrollment__academic_unit','enrollment__semester__academic_year','enrollment__user')[:20]
            records = [r for r in records if cls.eligible(r.enrollment)]
            if len(records) == 1:
                r = records[0]
                matches.append({'enrollment_id':r.enrollment_id,'unit_id':r.academic_unit_id,
                    'semester_id':r.semester_id,'unit_name':r.academic_unit.name,
                    'topic':r.academic_unit.name,'excerpt':text[:1000],'explicit_unit':False})
        return {'matches': matches[:8], 'ambiguous': ambiguous[:4], 'history_query': history}

    @classmethod
    @transaction.atomic
    def get_record(cls, user, enrollment):
        enrollment = cls.enrollments(user).select_for_update(of=('self',)).filter(pk=enrollment.pk).first()
        if not enrollment or not cls.eligible(enrollment):
            raise ValueError('A current, term-specific student enrollment is required')
        record, _ = SparkUnitLearningRecord.objects.get_or_create(enrollment=enrollment, semester=enrollment.semester,
            defaults={'user': user, 'academic_unit': enrollment.academic_unit})
        cls.require_writable(record, user)
        return record

    @classmethod
    def _session(cls, user, record, *, new=False, conversation=None):
        from pwanimate.services.study_session import StudySessionService
        cls.require_writable(record, user)
        if conversation is None:
            session = None if new else record.sessions.filter(user=user, status__in=['active','paused']).order_by('-started_at').first()
            if session:
                return session
            conversation = PwanimateConversation.objects.create(user=user, title=f'Study — {record.academic_unit.name}')
        if conversation.user_id != user.pk:
            raise LookupError('Conversation not owned')
        from pwanimate.services.study_context import require_session
        selected=require_session(user,conversation)
        if selected:
            if selected.learning_record_id and selected.learning_record_id != record.pk:
                raise ValueError('Choose a new conversation for a different academic unit.')
            if selected.status != 'active':
                return selected  # Resolving history/authorization never resumes it.
            if not selected.learning_record_id:
                if selected.unit_id and selected.unit_id!=record.academic_unit_id:
                    raise ValueError('Choose a new session for a different unit.')
                # Called on explicit approval; history is not imported.
                selected.learning_record=record
                selected.unit=record.academic_unit
                selected.academic_conversation=conversation
                selected.unit_scope=f'sals:{record.pk}:{conversation.pk}'
                selected.save(update_fields=['learning_record','unit','academic_conversation','unit_scope'])
            return selected
        target = conversation
        if PwanimateStudySession.objects.filter(conversation=conversation).exists():
            target = PwanimateConversation.objects.create(user=user, title=f'Study — {record.academic_unit.name}')
        session = record.sessions.filter(user=user, conversation=conversation, status='active').first()
        if session:
            return session
        session = PwanimateStudySession.objects.create(user=user, conversation=target,
            academic_conversation=conversation, learning_record=record, unit=record.academic_unit,
            unit_label=record.academic_unit.name,
            unit_scope=f'sals:{record.pk}:{conversation.pk}',
            learning_objective=f'Study {record.academic_unit.name}', status='active')
        return session

    @classmethod
    @transaction.atomic
    def approve_action(cls, action):
        """Called only inside ActionService.confirm's atomic owner-checked decision."""
        from pwanimate.services.actions import ActionError
        PwanimateConversation.objects.select_for_update().get(pk=action.conversation_id,user=action.user)
        pref = SparkAcademicTrackingPreference.objects.select_for_update().get(user=action.user)
        params = action.params
        if pref.mode == 'disabled' or params.get('tracking_revision') != pref.revision:
            raise ActionError('Tracking consent changed. Review a fresh request.')
        enrollment = cls.enrollments(action.user).select_for_update(of=('self',)).filter(pk=params.get('enrollment_id')).first()
        if not enrollment or not cls.eligible(enrollment) or enrollment.semester_id != params.get('semester_id'):
            raise ActionError('This student enrollment/term is no longer eligible.')
        source = PwanimateMessage.objects.filter(pk=params.get('source_message_id'), role='user',
            conversation=action.conversation, conversation__user=action.user).first()
        if not source or not any(item['enrollment_id'] == enrollment.pk for item in (cls.detect(action.user, source.content)['matches'] or cls.document_match(action.user,source.content,params.get('document_id'),enrollment_id=enrollment.pk))):
            raise ActionError('The source no longer supports this unit attribution.')
        record = cls.get_record(action.user, enrollment)
        record.authorized_at = timezone.now()
        record.consent_revision = pref.revision
        record.authorized_conversation = action.conversation
        record.save()
        session = cls._session(action.user, record, conversation=action.conversation)
        # Do not import the pre-approval source as learning evidence.
        return {'record_id': str(record.pk), 'session_id': str(session.pk),
            'tracking_from': record.authorized_at.isoformat(), 'historical_content_imported': False}

    @classmethod
    def document_match(cls,user,text,document_id,*,enrollment_id=None):
        from documents.models import Document
        from pwanimate.services.study_session import is_user_authorized_for_document
        document=Document.objects.filter(pk=document_id).first()
        if not document or not is_user_authorized_for_document(user,document):return []
        if not re.search(r'\b(teach|explain|study|derive|practice|help|what|how|show|summari[sz]e|continue)\b',text,re.I):return []
        unit_ids=list(document.academic_units.values_list('academic_unit_id',flat=True))
        eligible=[e for e in cls.enrollments(user).filter(academic_unit_id__in=unit_ids) if cls.eligible(e)]
        if enrollment_id is not None:
            eligible=[e for e in eligible if e.pk==enrollment_id]
        if len(eligible)!=1:return []
        e=eligible[0]
        return [{'enrollment_id':e.pk,'unit_id':e.academic_unit_id,'semester_id':e.semester_id,
            'unit_name':e.academic_unit.name,'topic':text[:255],'excerpt':text[:1000],'explicit_unit':False}]

    @classmethod
    def contextual_match(cls,session,user,text):
        if not session or not session.learning_record_id or session.user_id!=user.pk or session.status!='active':return []
        if not re.fullmatch(r"(?:continue|next|go on|another example|show me (?:another|an) example|explain (?:it |that )?again|simpler(?: please)?|(?:give me (?:a )?)?hint|practice)[.!? ]*",text.strip(),re.I):return []
        from pwanimate.models import SparkTeachingTurn
        latest=SparkTeachingTurn.objects.filter(state__session=session,completed_at__isnull=False).order_by('-completed_at').first()
        if not latest:return []
        record=session.learning_record
        topic=latest.payload.get('teaching',{}).get('objective') or session.current_topic or record.academic_unit.name
        return [{'enrollment_id':record.enrollment_id,'unit_id':record.academic_unit_id,'semester_id':record.semester_id,
            'unit_name':record.academic_unit.name,'topic':topic[:255],'excerpt':text[:1000],'explicit_unit':False}]

    @classmethod
    def scoped_attribution(cls,session,user,text,resources=None):
        matches=cls.detect(user,text)['matches']
        if matches:return any(m['enrollment_id']==session.learning_record.enrollment_id for m in matches)
        if cls.contextual_match(session,user,text):return True
        from pwanimate.orchestrator.document_context import DocumentTurnContext
        context=DocumentTurnContext.resolve(user,resources or [],text)
        for resource in context.resolutions:
            if resource.resolved and any(m['enrollment_id']==session.learning_record.enrollment_id for m in cls.document_match(user,text,resource.document_id,enrollment_id=session.learning_record.enrollment_id)):
                return True
        return False

    @classmethod
    @transaction.atomic
    def process_message(cls, message_id, *, document_id=None):
        from pwanimate.services.actions import ActionService
        message = PwanimateMessage.objects.select_related('conversation__user').filter(pk=message_id, role='user').first()
        if not message:
            return {'matches': [], 'ambiguous': []}
        user = message.conversation.user
        PwanimateConversation.objects.select_for_update().get(pk=message.conversation_id,user=user)
        from pwanimate.services.study_context import resolve
        canonical=resolve(user,message.conversation)
        if canonical.session is None and canonical.candidates:
            raise ValueError('Choose the study session before recording activity.')
        if canonical.session and canonical.session.status!='active':
            return cls.detect(user,message.content)
        detection = cls.detect(user, message.content)
        if not detection['matches'] and canonical.tracking=='authorized':
            detection['matches']=cls.contextual_match(canonical.session,user,message.content)
        if not detection['matches'] and document_id:
            detection['matches']=cls.document_match(user,message.content,document_id,enrollment_id=canonical.session.learning_record.enrollment_id if canonical.session and canonical.session.learning_record_id else None)
        if detection['history_query'] or not detection['matches']:
            return detection
        SparkAcademicTrackingPreference.objects.get_or_create(user=user, defaults={'changed_at': message.created_at})
        with transaction.atomic():
            pref = SparkAcademicTrackingPreference.objects.select_for_update().get(user=user)
            if pref.mode == 'disabled' or message.created_at < pref.changed_at:
                return detection
            for match in detection['matches']:
                enrollment = cls.enrollments(user).select_for_update(of=('self',)).get(pk=match['enrollment_id'])
                if not cls.eligible(enrollment) or enrollment.semester_id != match['semester_id']:
                    continue
                record = SparkUnitLearningRecord.objects.filter(user=user, enrollment=enrollment, semester=enrollment.semester).first()
                authorized = bool(record and record.authorized_at and record.consent_revision == pref.revision
                    and message.created_at > record.authorized_at)
                if authorized and pref.mode == 'ask':
                    authorized = record.authorized_conversation_id == message.conversation_id and record.sessions.filter(status='active').exists()
                if not authorized:
                    existing = PendingAction.objects.filter(user=user, conversation=message.conversation,
                        action_type=PendingAction.ACTION_TRACK_UNIT, params__enrollment_id=enrollment.pk,
                        params__semester_id=enrollment.semester_id, params__tracking_revision=pref.revision)
                    if existing.filter(status='cancelled').exists() or existing.filter(status='pending', expires_at__gt=timezone.now()).exists():
                        continue
                    ActionService.propose(user, PendingAction.ACTION_TRACK_UNIT, {
                        'enrollment_id': enrollment.pk, 'semester_id': enrollment.semester_id,
                        'tracking_revision': pref.revision, 'source_message_id': message.pk, 'document_id':document_id},
                        summary=f'Would you like to track future {enrollment.academic_unit.name} study activity in {enrollment.semester}? Earlier messages will not be imported.',
                        conversation=message.conversation)
                    continue
                session = cls._session(user, record, conversation=message.conversation)
                kind = 'difficulty' if re.match(r'^(?:spark[,!:]\s*)?I (?:struggle|have difficulty)\b', match['excerpt'], re.I) else \
                    'self_report' if re.match(r'^(?:spark[,!:]\s*)?I understand\b', match['excerpt'], re.I) else 'study'
                SparkUnitActivity.objects.get_or_create(record=record, source_message=message, topic=match['topic'],
                    defaults={'session': session, 'source_conversation': message.conversation,
                        'excerpt': match['excerpt'], 'kind': kind, 'consent_revision': pref.revision,
                        'occurred_at': message.created_at})
                session.last_active_at = timezone.now()
                session.save(update_fields=['last_active_at'])
        return detection

    @classmethod
    def process_safely(cls, message_id):
        try:
            return cls.process_message(message_id)
        except Exception:
            logger.exception('Optional unit tracking unavailable message=%s', message_id)
            return {'matches': [], 'ambiguous': []}

    @classmethod
    def summary(cls, record, user):
        if record.user_id != user.pk:
            raise LookupError('Learning record not found')
        activities = list(record.activities.select_related('source_message').order_by('-occurred_at')[:100])
        activities.reverse()
        sessions = list(record.sessions.filter(user=user).order_by('started_at')[:50])
        from pwanimate.models import SparkAssessment, SparkPersonalMemory
        observations = SparkPersonalMemory.objects.filter(user=user, status='approved',
            source_message_id__in=[activity.source_message_id for activity in activities if activity.source_message_id],
            purpose='academic_learning')[:50]
        observations = [fact for fact in observations if any(a.source_message_id == fact.source_message_id
            and contains(normalized(fact.text), normalized(a.topic)) for a in activities)][:12]
        try:
            cls.require_writable(record, user)
            read_only = False
        except ValueError:
            read_only = True
        assessments = []
        from pwanimate.services.assessment import public_assessment
        for assessment in SparkAssessment.objects.filter(user=user, session__in=sessions)[:30]:
            public = public_assessment(assessment)
            assessments.append({key: public[key] for key in ['id', 'title', 'status', 'total',
                'answered', 'correct', 'certification_status']})
        from pwanimate.services.academic_progress import progress_summary
        progress = progress_summary(record, user)
        return {**progress, 'id': str(record.pk), 'enrollment_id': record.enrollment_id,
            'unit_id': record.academic_unit_id, 'unit_name': record.academic_unit.name,
            'unit_code': record.academic_unit.code, 'semester_id': record.semester_id,
            'term': str(record.semester), 'read_only': read_only,
            'topics': list(dict.fromkeys([activity.topic for activity in activities] + progress['covered_topics'])),
            'requested_topics':list(dict.fromkeys(a.topic for a in activities)),
            'topics_semantics':'Request observations and completed exposure; covered_topics identifies finalized teaching.',
            'activities': [{'id': str(a.pk), 'topic': a.topic, 'excerpt': a.excerpt,
                'kind': a.kind, 'occurred_at': a.occurred_at.isoformat(), 'session_id': str(a.session_id),
                'conversation_id': str(a.source_conversation_id) if a.source_conversation_id else None,
                'message_id': a.source_message_id} for a in activities],
            'sessions': [{'id': str(s.pk), 'status': s.status, 'conversation_id': str(s.academic_conversation_id or s.conversation_id),
                'home_conversation_id':str(s.conversation_id),
                'started_at': s.started_at.isoformat(), 'ended_at': s.ended_at.isoformat() if s.ended_at else None,
                'roadmap': s.roadmap or []} for s in sessions],
            'assessments': assessments,
            'approved_observations': [{'id': str(f.pk), 'text': f.text, 'evidence_type': 'self_report'} for f in observations],
            'difficulties': [a.topic for a in activities if a.kind == 'difficulty'],
            'understanding_self_reports': [a.topic for a in activities if a.kind == 'self_report'],
            'outstanding_roadmap_items': [item.get('label') for s in sessions for item in s.roadmap or []
                if isinstance(item, dict) and item.get('status') != 'done'][:30],
            'history_truncated': record.activities.count() > 100}

    @classmethod
    def context_for_query(cls, user, query):
        """No broad transcript recall. Select a named unit or a resolved enrolled topic."""
        if not user or not user.is_authenticated:
            return []
        pref = SparkAcademicTrackingPreference.objects.filter(user=user).first()
        if pref is None or pref.mode == 'disabled':
            return []  # Model recall opt-out; owner may still inspect retained rows.
        query_text = normalized(query)
        matched = {m['unit_id'] for m in cls.detect(user, query)['matches']}
        records = SparkUnitLearningRecord.objects.filter(user=user).select_related('academic_unit', 'semester__academic_year', 'enrollment__user','enrollment__academic_unit','enrollment__semester__academic_year').order_by('-semester__start_date')
        records = list(records[:100])
        named = [r for r in records if r.academic_unit_id in matched or contains(query_text, normalized(r.academic_unit.name)) or contains(query_text, normalized(r.academic_unit.code))]
        current = [r for r in records if r.authorized_at and r.consent_revision == pref.revision and cls.eligible(r.enrollment)]
        continuity = bool(HISTORY.search(query) or re.search(r'previous lesson|last time|continue', query, re.I))
        from pwanimate.services.recall_intent import is_recent_learning_recall
        if not named and is_recent_learning_recall(query):
            from pwanimate.models import SparkTeachingTurn
            # Choose actual completed activity, not the most recently created
            # session/record. Retained history grants no new write permission.
            latest = SparkTeachingTurn.objects.filter(
                state__session__user=user,
                state__session__learning_record_id__in=[r.pk for r in records if r.authorized_at],
                completed_at__isnull=False, completed_message__conversation__user=user,
                completed_message__role='assistant',
            ).order_by('-completed_at', '-pk').values_list('state__session__learning_record_id', flat=True).first()
            named = [r for r in records if r.pk == latest]
        elif not named and continuity:
            if len(current) == 1:
                named = current
            elif len(current) > 1:
                return [{'clarification_required': True, 'units': [f'{r.academic_unit.code} — {r.academic_unit.name} ({r.semester})' for r in current[:8]]}]
        term_named = [r for r in named if contains(query_text, normalized(str(r.semester)))]
        if len(term_named) == 1:
            named = term_named
        if len(named) > 1:
            return [{'clarification_required': True, 'units': [f'{r.academic_unit.code} — {r.academic_unit.name} ({r.semester})' for r in named[:8]]}]
        selected = []
        for record in named:
            if record.authorized_at:
                summary = cls.summary(record, user)
                summary['activities'] = summary['activities'][-10:]
                summary['historical_read'] = True
                # This projection supplies evidence, not the current turn's
                # enrollment/consent authorization (checked separately).
                summary['read_only'] = True
                summary['write_authorization'] = 'History read grants no new writes; check current conversation consent separately.'
                selected.append(summary)
                if len(selected) == 3:
                    break
        return selected

    @classmethod
    def safe_context(cls, user, query):
        try:
            return cls.context_for_query(user, query)
        except Exception:
            logger.exception('Optional academic context unavailable user=%s', getattr(user, 'pk', None))
            return []

    @classmethod
    def _action_unit_names(cls, actions, user):
        """Resolve pending/declined proposal params to enrolled unit names."""
        params = [a.params for a in actions if isinstance(a.params, dict)]
        enrollment_ids = {p.get('enrollment_id') for p in params if p.get('enrollment_id')}
        enrollments = {e.pk: e for e in cls.enrollments(user).filter(pk__in=enrollment_ids)
            .select_related('academic_unit', 'semester')}
        names = []
        for action in actions:
            enrollment = enrollments.get((action.params or {}).get('enrollment_id'))
            if enrollment is None:
                continue
            label = f"{enrollment.academic_unit.name} ({enrollment.semester})"
            if label not in names:
                names.append(label)
        return names[:5]

    @classmethod
    def tracking_state(cls, user, conversation=None):
        """Server-verified academic-tracking posture for prompt context.

        Distinguishes legacy Study Mode (carried separately on the request),
        authorized unit-linked sessions that are actually recording, consent
        cards that are persisted and renderable right now, and declined or
        disabled tracking. The model must describe only what this returns —
        it must never promise an approval card that does not exist or claim
        tracking is active without an authorized session.
        """
        state = {'preference_mode': None, 'tracking_disabled': False,
                 'active_unit_tracking': [], 'pending_consents': [],
                 'declined_units': []}
        if not user or not user.is_authenticated:
            return state
        try:
            pref = SparkAcademicTrackingPreference.objects.filter(user=user).first()
            state['preference_mode'] = pref.mode if pref else 'ask'
            state['tracking_disabled'] = bool(pref and pref.mode == 'disabled')
            if pref is None or pref.mode == 'disabled':
                return state
            now = timezone.now()
            pending_qs = PendingAction.objects.filter(
                user=user, action_type=PendingAction.ACTION_TRACK_UNIT,
                status=PendingAction.STATUS_PENDING, expires_at__gt=now, params__tracking_revision=pref.revision)
            if conversation is not None:
                pending_qs = pending_qs.filter(conversation=conversation)
            state['pending_consents'] = cls._action_unit_names(list(pending_qs[:10]), user)
            records = SparkUnitLearningRecord.objects.filter(
                user=user, authorized_at__isnull=False,
                consent_revision=pref.revision).select_related(
                'academic_unit', 'semester').prefetch_related('sessions')[:20]
            for record in records:
                try:
                    cls.require_writable(record, user)
                except (ValueError, LookupError):
                    continue
                session = record.sessions.filter(user=user, status='active').order_by('-started_at').first()
                if session is None:
                    continue
                if pref.mode == 'ask' and conversation is not None \
                        and record.authorized_conversation_id != conversation.pk:
                    continue
                label = f"{record.academic_unit.name} ({record.semester})"
                if label not in state['active_unit_tracking'] and len(state['active_unit_tracking']) < 5:
                    state['active_unit_tracking'].append(label)
            declined = PendingAction.objects.filter(
                user=user, action_type=PendingAction.ACTION_TRACK_UNIT,
                status=PendingAction.STATUS_CANCELLED).order_by('-created_at')[:20]
            state['declined_units'] = cls._action_unit_names(list(declined), user)
        except Exception:
            logger.exception('Tracking state unavailable user=%s', getattr(user, 'pk', None))
        return state
