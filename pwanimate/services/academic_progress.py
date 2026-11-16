"""Projection of durable teaching outcomes, not a second learning-evidence store."""
from django.db import transaction
from django.utils import timezone
from pwanimate.models import SparkTeachingTurn, SparkAgentInteraction

@transaction.atomic
def finalize(turn_id, message, *, complete):
    from pwanimate.models import PwanimateConversation, PwanimateStudySession
    PwanimateConversation.objects.select_for_update().get(pk=message.conversation_id,user_id=message.conversation.user_id)
    # Read identity first; acquire shared authorization before any child lock.
    turn = SparkTeachingTurn.objects.select_related('state__session__learning_record').get(pk=turn_id)
    session = turn.state.session
    from pwanimate.services.study_context import resolve
    selected=resolve(session.user,message.conversation).session
    if selected is None or selected.pk!=session.pk:
        return
    if session.user_id != message.conversation.user_id or (session.conversation_id != message.conversation_id and session.academic_conversation_id != message.conversation_id):
        raise ValueError('Teaching completion does not own this conversation')
    if turn.completed_at or not complete or not session.learning_record_id or session.status != 'active':
        return
    from pwanimate.services.concept_mastery import ConceptMasteryService
    from pwanimate.models import PwanimateMessage, SparkAcademicTrackingPreference
    try:
        record = ConceptMasteryService.lock_record(session.user, session.learning_record_id)
    except (ValueError, LookupError):
        return  # Tutoring may finish untracked after revocation.
    authorized_record_id=session.learning_record_id
    session=PwanimateStudySession.objects.select_for_update(of=('self',)).get(pk=session.pk)
    turn=SparkTeachingTurn.objects.select_for_update(of=('self',)).select_related('completed_message').get(pk=turn.pk)
    if session.learning_record_id != authorized_record_id or turn.state.session_id != session.pk:
        raise ValueError('Teaching completion binding changed')
    if turn.completed_at or session.status != 'active':
        return
    pref = SparkAcademicTrackingPreference.objects.get(user=session.user)
    if pref.mode == 'ask' and record.authorized_conversation_id != message.conversation_id:
        raise ValueError('This conversation is not authorized for capture')
    # Approval never retroactively authorizes the prompting message/interaction.
    source_id = turn.receipt_key.removeprefix('message:') if turn.receipt_key.startswith('message:') else None
    source_time = PwanimateMessage.objects.filter(pk=source_id).values_list('created_at',flat=True).first() if source_id else turn.answer_interaction.resolved_at if turn.answer_interaction_id else None
    if source_time is None or source_time <= record.authorized_at:
        return
    turn.completed_message = message
    turn.completed_at = timezone.now()
    turn.save(update_fields=['completed_message','completed_at'])


def progress_summary(record, user):
    from pwanimate.services.concept_mastery import ConceptMasteryService
    turns = list(SparkTeachingTurn.objects.filter(state__session__learning_record=record,
        state__session__user=user, completed_at__isnull=False).select_related('state__concept','state__session',
        'answer_interaction','question_interaction','completed_message').order_by('-completed_at','id')[:30])
    turns.reverse()
    progress=[]
    for turn in turns:
        snapshot=turn.payload.get('teaching',{})
        progress.append({'id':str(turn.pk),'session_id':str(turn.state.session_id),
            'topic': snapshot.get('objective',''), 'concept_id':snapshot.get('concept_id'),
            'phase':turn.to_phase,'action':turn.action,'kind':turn.activity_kind,
            'assistance':turn.assistance,'hint_level':snapshot.get('hint_level',0),
            'sources':turn.completed_message.sources[:10] if turn.completed_message_id else [],
            'citations':turn.completed_message.citations[:10] if turn.completed_message_id else [],
            'feedback':turn.response_markdown[:1000], 'evaluation':turn.evaluation,'student_answer':turn.answer_interaction.answer if turn.answer_interaction_id else None,
            'assigned_exercise':(turn.payload.get('context') or {}).get('exercise'),
            'outstanding_interaction_id':str(turn.question_interaction_id) if turn.question_interaction_id and turn.question_interaction.status=='pending' else None,
            'completed_at':turn.completed_at.isoformat(),'message_id':turn.completed_message_id,
            'evidence_semantics':'teaching progress; not independent mastery'})
    # Evidence must pass WP1 provenance checks; a declared verification flag alone is insufficient.
    concepts=list(record.concepts.all()[:50])
    from pwanimate.models import SparkConceptEvidence
    evidence = SparkConceptEvidence.objects.filter(concept__record=record).select_related('concept','question','session').order_by('-occurred_at')[:100]
    assessed = False
    for item in evidence:
        row = ConceptMasteryService.evidence_payload(item)
        if row['currently_usable'] and row['currently_verified']:
            assessed = True
            break
    return {'teaching_progress':progress,'covered_topics':list(dict.fromkeys(p['topic'] for p in progress if p['topic'])),
        'last_covered_concept':progress[-1] if progress else None,
        'outstanding_checkpoints':[p for p in progress if p['outstanding_interaction_id']],
        'mastery_assessed':assessed,'concept_mastery':ConceptMasteryService.unit_summary(user,record.pk),
        'projection_scope':'post-migration finalized teaching turns only'}

@transaction.atomic
def finalize_ordinary(source_id, message, *, complete):
    """Explicitly classify a delivered attributed answer as exposure, not grading.

    Only an already-authorized request activity can supply provenance. No chat
    reconstruction and no extraction of correctness from generated prose.
    """
    if not complete:
        return
    from pwanimate.models import SparkUnitActivity
    from pwanimate.services.teaching import TeachingService
    activities=list(SparkUnitActivity.objects.filter(source_message_id=source_id,
        record__user=message.conversation.user, source_conversation=message.conversation).select_related('record','session')[:2])
    if len(activities) != 1 or not activities[0].session_id:
        return
    activity=activities[0]
    # Creating a state/turn also obtains FK locks. Respect the same parent-first
    # order as teaching preparation before creating those child receipts.
    from pwanimate.models import PwanimateConversation
    from pwanimate.services.concept_mastery import ConceptMasteryService
    PwanimateConversation.objects.select_for_update().get(pk=message.conversation_id,user=message.conversation.user)
    try:
        ConceptMasteryService.lock_record(message.conversation.user,activity.record_id)
    except (ValueError,LookupError):
        return
    from pwanimate.models import PwanimateStudySession
    session=PwanimateStudySession.objects.select_for_update(of=('self',)).get(pk=activity.session_id,user=message.conversation.user)
    if session.status != 'active' or session.learning_record_id != activity.record_id:
        return
    state=TeachingService.state_for(session,message.conversation.user)
    snapshot={'objective':activity.topic, 'concept_id':None, 'phase':'EXPLAIN', 'hint_level':0}
    turn,_=SparkTeachingTurn.objects.get_or_create(state=state,receipt_key='message:'+str(source_id), defaults={
        'from_phase':state.phase,'to_phase':'EXPLAIN','action':'ordinary_answer',
        'activity_kind':'topic_exposure','assistance':'unknown',
        'payload':{'teaching':snapshot},'response_markdown':message.content})
    finalize(turn.pk,message,complete=True)
