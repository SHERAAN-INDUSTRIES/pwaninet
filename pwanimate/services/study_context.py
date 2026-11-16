"""Canonical session selection. Resolution is read-only; selection is explicit."""
from dataclasses import dataclass
from django.db import transaction
from django.db.models import Q
from pwanimate.models import PwanimateConversation, PwanimateStudySession, SparkAcademicTrackingPreference, SparkTeachingState, SparkAgentInteraction

from rest_framework.exceptions import APIException
from pwanimate.services.agent_runs import RunError

class StudyConflict(APIException,RunError):
    status_code=409
    default_detail='Refresh and select the study session.'
    default_code='study_conflict'

@dataclass
class ResolvedStudyContext:
    conversation: object
    session: object
    generation: int
    candidates: list
    tracking: str = 'untracked'

    def payload(self):
        state = SparkTeachingState.objects.filter(session=self.session).first() if self.session else None
        from pwanimate.models import SparkAgentRun
        waiting=list(SparkAgentRun.objects.filter(user_id=self.conversation.user_id,user_message__conversation=self.conversation,status='waiting').order_by('created_at')[:2])
        managed=set(SparkTeachingState.objects.filter(active_run__in=waiting).exclude(session=self.session).values_list('active_run_id',flat=True))
        waiting=[run for run in waiting if run.pk not in managed]
        active=state.active_run if state and state.active_run_id else None
        pending_run=active if active and active.status in ['waiting','running','queued'] else (waiting[0] if len(waiting)==1 else None)
        interaction = SparkAgentInteraction.objects.filter(run_id=state.active_run_id,status='pending').first() if state and state.active_run_id else None
        progress=None
        academic=None
        if self.session and self.session.learning_record_id:
            record=self.session.learning_record
            academic={'record_id':str(record.pk),'enrollment_id':record.enrollment_id,'unit_id':record.academic_unit_id,'semester_id':record.semester_id}
            if self.tracking!='disabled':
                from pwanimate.services.unit_learning import UnitLearningService
                summary=UnitLearningService.summary(record,self.session.user)
                progress={k:summary[k] for k in ['last_covered_concept','outstanding_checkpoints','concept_mastery','mastery_assessed']}
        from pwanimate.orchestrator.document_context import DocumentTurnContext
        resources=(self.session.context_state or {}).get('resources',[])[:10] if self.session else []
        documents=DocumentTurnContext.resolve(self.session.user,resources,'study') if self.session else None
        return {'academic':academic,'retained_progress':progress,
            'document_context':[r.to_status_dict() for r in documents.resolutions] if documents else [],
            'conversation_id':str(self.conversation.pk),'session_id':str(self.session.pk) if self.session else None,
            'generation':self.generation,'ambiguous':self.session is None and len(self.candidates)>1,
            'status':self.session.status if self.session else None,'tracking':self.tracking,
            'record_id':str(self.session.learning_record_id) if self.session and self.session.learning_record_id else None,
            'candidates':[{'id':str(s.pk),'status':s.status,'unit':s.unit_label,'topic':s.current_topic or s.learning_objective,'started_at':s.started_at.isoformat(),'tracked':bool(s.learning_record_id)} for s in self.candidates],
            'pending_run_id':str(pending_run.pk) if pending_run else None,
            'pending_interaction':interaction.serialize() if interaction else None,
            'checkpoint_id':str(self.session.latest_checkpoint_id) if self.session and self.session.latest_checkpoint_id else None,
            'resources':(self.session.context_state or {}).get('resources',[]) if self.session else []}


def resolve(user, conversation):
    if not user or not user.is_authenticated or conversation.user_id != user.pk:
        raise StudyConflict('Conversation is not owned.')
    conversation=PwanimateConversation.objects.get(pk=conversation.pk,user=user)
    candidates=list(PwanimateStudySession.objects.filter(user=user).filter(
        Q(conversation=conversation)|Q(academic_conversation=conversation)).select_related('learning_record','unit').order_by('pk'))
    selected=next((s for s in candidates if s.pk==conversation.canonical_study_session_id),None)
    if conversation.canonical_study_session_id and selected is None:
        raise StudyConflict('Selected session binding is no longer valid.')
    if selected is None and len(candidates)==1:
        selected=candidates[0]
    tracking='untracked'
    if selected and selected.learning_record_id:
        from pwanimate.services.unit_learning import UnitLearningService
        pref=SparkAcademicTrackingPreference.objects.filter(user=user).first()
        record=selected.learning_record
        tracking='needs_consent'
        try:
            UnitLearningService.require_writable(record,user)
            if pref and pref.mode!='disabled' and record.authorized_at and record.consent_revision==pref.revision and (pref.mode=='automatic' or record.authorized_conversation_id==conversation.pk):
                tracking='authorized'
        except (ValueError,LookupError):
            tracking='read_only'
        if pref and pref.mode=='disabled': tracking='disabled'
    return ResolvedStudyContext(conversation,selected,conversation.study_binding_generation,candidates,tracking)


@transaction.atomic
def select(user,conversation,session_id,generation):
    conv=PwanimateConversation.objects.select_for_update().get(pk=conversation.pk,user=user)
    if type(generation) is not int or conv.study_binding_generation!=generation:
        raise StudyConflict('Session selection changed. Refresh and choose again.')
    context=resolve(user,conv)
    sessions=list(PwanimateStudySession.objects.select_for_update(of=('self',)).filter(pk__in=[s.pk for s in context.candidates]).order_by('pk'))
    selected=next((s for s in sessions if str(s.pk)==str(session_id)),None)
    if selected is None: raise StudyConflict('Session is not bound to this conversation.')
    from pwanimate.models import SparkAgentRun
    scoped=set(SparkTeachingState.objects.filter(session__in=sessions).exclude(active_run_id=None).values_list('active_run_id',flat=True))
    if SparkAgentRun.objects.filter(user=user,user_message__conversation=conv,status__in=['waiting','running','queued']).exclude(pk__in=scoped).exists():
        raise StudyConflict('Resolve or cancel the current task before handing off this conversation.')
    for session in sessions:
        if session.pk==selected.pk: continue
        if SparkTeachingState.objects.filter(session=session,active_run__status__in=['running','queued']).exists():
            raise StudyConflict('Wait for the current execution to finish before switching sessions.')
        if session.status=='active':
            session.status='paused'
            session.save(update_fields=['status','updated_at'])
    conv.canonical_study_session=selected
    conv.study_binding_generation+=1
    conv.save(update_fields=['canonical_study_session','study_binding_generation'])
    return resolve(user,conv)


def require_session(user,conversation):
    context=resolve(user,conversation)
    if context.session is None and context.candidates:
        raise StudyConflict('Choose the study session to continue.')
    return context.session


def fence_run(user,run):
    state_id=(run.state or {}).get('teaching_state_id')
    if not state_id:return
    state=SparkTeachingState.objects.select_related('session').filter(pk=state_id,session__user=user).first()
    if not state or state.session.status!='active':raise StudyConflict('Resume this study session before continuing its question.')
    context=resolve(user,run.user_message.conversation)
    if not context.session or context.session.pk!=state.session_id:
        raise StudyConflict('This question belongs to a different selected study session.')


def lifecycle_target(user,session):
    context=resolve(user,session.academic_conversation or session.conversation)
    if context.session is None or context.session.pk!=session.pk:
        raise StudyConflict('Select this session explicitly before changing its lifecycle.')
    return context

@transaction.atomic
def request_tracking(user,conversation,generation):
    """Renew a session's grant without consuming its pending teaching question."""
    from pwanimate.models import PendingAction,PwanimateMessage
    from pwanimate.services.actions import ActionService
    from django.utils import timezone
    conv=PwanimateConversation.objects.select_for_update().get(pk=conversation.pk,user=user)
    context=resolve(user,conv)
    if type(generation) is not int or conv.study_binding_generation!=generation:raise StudyConflict('Refresh the selected session.')
    if not context.session or not context.session.learning_record_id:raise StudyConflict('Choose an enrolled academic session first.')
    if context.tracking=='authorized':return []
    record=context.session.learning_record
    from pwanimate.services.unit_learning import UnitLearningService
    UnitLearningService.require_writable(record,user)
    pref=SparkAcademicTrackingPreference.objects.filter(user=user).first()
    if not pref or pref.mode=='disabled':raise StudyConflict('Enable academic tracking in settings first.')
    existing=PendingAction.objects.filter(user=user,conversation=conv,action_type=PendingAction.ACTION_TRACK_UNIT,
        status='pending',expires_at__gt=timezone.now(),params__enrollment_id=record.enrollment_id,params__tracking_revision=pref.revision).first()
    if existing:return [ActionService.serialize(existing)]
    source=PwanimateMessage.objects.create(conversation=conv,role='user',task='academic_authorization',content='Study '+record.academic_unit.code)
    action=ActionService.propose(user,PendingAction.ACTION_TRACK_UNIT,
        {'enrollment_id':record.enrollment_id,'semester_id':record.semester_id,'tracking_revision':pref.revision,'source_message_id':source.pk,'document_id':None},
        summary='Authorize future progress in this session. Existing questions and history will not be imported or answered.',conversation=conv)
    return [ActionService.serialize(action)]
