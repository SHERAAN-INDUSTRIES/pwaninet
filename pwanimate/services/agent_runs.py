"""Durable approvals and lifecycle for the exact actions shown in one Spark task."""
import json
from datetime import timedelta
from django.db import transaction
from django.core.serializers.json import DjangoJSONEncoder
from django.utils import timezone
from pwanimate.models import SparkAgentRun, PendingAction, SparkAgentInteraction
from pwanimate.services.actions import ActionService, ActionError

SNAPSHOT_FIELDS = ['snapshot_version','study_mode','execution_boundary_id','query','history','task','provider','model','engine','fallback_chain','offering',
 'temperature','max_tokens','context_resources','local_time','timezone_name','conversation_memory','request_id','user_message_id']

# Server-authoritative lifecycle. 'waiting' is the single paused status;
# wait_reason ('clarification' | 'approval') says what it is waiting for, so
# historical rows and clients keep working without a status migration.
TRANSITIONS = {
    SparkAgentRun.STATUS_QUEUED: {'running', 'cancelled', 'failed', 'expired'},
    SparkAgentRun.STATUS_RUNNING: {'waiting', 'completed', 'failed', 'interrupted', 'cancelled', 'expired'},
    SparkAgentRun.STATUS_WAITING: {'running', 'cancelled', 'failed', 'expired'},
    SparkAgentRun.STATUS_INTERRUPTED: {'running', 'cancelled', 'failed', 'expired'},
    SparkAgentRun.STATUS_COMPLETED: set(),
    SparkAgentRun.STATUS_FAILED: set(),
    SparkAgentRun.STATUS_CANCELLED: set(),
    SparkAgentRun.STATUS_EXPIRED: set(),
}
TERMINAL_STATUSES = {
    SparkAgentRun.STATUS_COMPLETED, SparkAgentRun.STATUS_FAILED,
    SparkAgentRun.STATUS_CANCELLED, SparkAgentRun.STATUS_EXPIRED,
}
# How long an execution attempt may hold a run before recovery marks it
# interrupted. Must exceed the agent deadline plus final generation.
RUN_LEASE_SECONDS = 300


class RunError(ValueError):
    pass


class TransitionError(RunError):
    pass


class AgentRunService:
    @staticmethod
    def start_document(request):
        """Start a file task using the existing run, lease and snapshot contract."""
        import uuid
        from pwanimate.models import PwanimateConversation, PwanimateMessage
        from pwanimate.services.run_snapshots import CURRENT_SNAPSHOT_VERSION
        snapshot = {k: getattr(request, k) for k in SNAPSHOT_FIELDS}
        snapshot['snapshot_version'] = CURRENT_SNAPSHOT_VERSION
        snapshot['history'] = [m.to_dict() for m in request.history[-10:]]
        snapshot['context_resources'] = list(snapshot.get('context_resources') or []) + [
            {'type': 'attachment', 'attachment_id': str(a.pk)} for a in request.attachments if getattr(a, 'pk', None)]
        snapshot = json.loads(json.dumps(snapshot, cls=DjangoJSONEncoder))
        with transaction.atomic():
            source = PwanimateMessage.objects.get(pk=request.user_message_id, role='user', conversation__user=request.user)
            PwanimateConversation.objects.select_for_update().get(pk=source.conversation_id, user=request.user)
            source = PwanimateMessage.objects.select_for_update(of=('self',)).get(pk=source.pk)
            existing = SparkAgentRun.objects.filter(user=request.user, user_message=source).first()
            if existing is not None:
                return existing, False
            now = timezone.now()
            run = SparkAgentRun.objects.create(user=request.user, user_message=source,
                snapshot=snapshot, state={'document_generation': True}, status='running',
                continuation={'kind': 'document_generation'},
                attempt_id=uuid.uuid4().hex, lease_expires_at=now + timedelta(seconds=RUN_LEASE_SECONDS),
                expires_at=now + timedelta(hours=2))
            return run, True

    @staticmethod
    def transition(run, to_status, *, wait_reason=None, update_fields=None):
        """Move a run through the lifecycle, rejecting illegal reversals.

        Terminal states can never be left. Call inside a transaction when the
        transition must be atomic with other writes (e.g. interaction claims).
        """
        if to_status == run.status:
            return run
        if to_status not in TRANSITIONS.get(run.status, set()):
            raise TransitionError(
                f"Cannot move agent run {run.id} from '{run.status}' to '{to_status}'.")
        run.status = to_status
        fields = {'status', 'updated_at'}
        if wait_reason is not None:
            run.wait_reason = wait_reason
            fields.add('wait_reason')
        run.save(update_fields=sorted(fields | set(update_fields or ())))
        return run

    @staticmethod
    def is_terminal(run):
        return run.status in TERMINAL_STATUSES

    @staticmethod
    def pause(request, state, actions, *, run=None):
        if not request.user or not request.user.is_authenticated or not request.user_message_id:
            raise RunError('A saved, signed-in request is required for approval.')
        snapshot = {k:getattr(request,k) for k in SNAPSHOT_FIELDS}
        from pwanimate.services.run_snapshots import CURRENT_SNAPSHOT_VERSION
        snapshot['snapshot_version'] = CURRENT_SNAPSHOT_VERSION
        snapshot['history'] = [m.to_dict() for m in request.history[-10:]]
        snapshot = json.loads(json.dumps(snapshot, cls=DjangoJSONEncoder))
        wait_reason = SparkAgentRun.WAIT_APPROVAL if actions else SparkAgentRun.WAIT_CLARIFICATION
        from pwanimate.models import PwanimateMessage
        with transaction.atomic():
            source = PwanimateMessage.objects.select_for_update().filter(
                pk=request.user_message_id, conversation__user=request.user, role='user').first()
            if source is None: raise RunError('This task is unavailable.')
            existing = SparkAgentRun.objects.select_for_update().filter(user_message=source,user=request.user).first()
            if existing is not None:
                if existing.status == SparkAgentRun.STATUS_WAITING: return existing
                if run is None or run.pk != existing.pk or existing.status != SparkAgentRun.STATUS_RUNNING:
                    raise RunError('This task has already continued.')
            values = {'snapshot':snapshot,'state':json.loads(json.dumps(state,cls=DjangoJSONEncoder)),
                'action_ids':[str(a['id']) for a in actions], 'status':'waiting','wait_reason':wait_reason,
                'cancel_requested':False,'continuation':{}, 'result_message':None,
                'expires_at':timezone.now()+timedelta(minutes=15)}
            if existing is None:
                run = SparkAgentRun.objects.create(user=request.user,user_message=source,**values)
            else:
                for key,value in values.items(): setattr(existing,key,value)
                existing.save(update_fields=[*values,'updated_at'])
                run = existing
        return run

    @staticmethod
    def serialize(run):
        actions = list(PendingAction.objects.filter(pk__in=run.action_ids, user=run.user))
        pending_interaction = SparkAgentInteraction.objects.filter(
            run=run, status=SparkAgentInteraction.STATUS_PENDING).order_by('-created_at').first()
        payload = {'id':str(run.id),'status':run.status,'wait_reason':run.wait_reason,
                'expires_at':run.expires_at.isoformat(),
                'actions':[ActionService.serialize(a) for a in actions]}
        if pending_interaction is not None:
            payload['interaction'] = pending_interaction.serialize()
        payload['resolved_interaction_ids'] = [str(i) for i in SparkAgentInteraction.objects.filter(
            run=run, user=run.user, status=SparkAgentInteraction.STATUS_ANSWERED).values_list('id',flat=True)]
        if run.result_message_id:
            payload['result_message_id'] = run.result_message_id
        return payload

    @classmethod
    def decide(cls, user, run_id, decision, action_ids):
        if decision not in {'confirm','cancel','confirm_all','cancel_all'}:
            raise RunError('Choose approve or decline.')
        if not isinstance(action_ids,list) or not action_ids or len(action_ids)>6 or not all(isinstance(x,str) for x in action_ids):
            raise RunError('Choose the exact actions shown for this task.')
        with transaction.atomic():
            from pwanimate.models import PwanimateConversation
            candidate=SparkAgentRun.objects.filter(pk=run_id,user=user).first()
            if candidate:
                PwanimateConversation.objects.select_for_update().get(pk=candidate.user_message.conversation_id,user=user)
            run = SparkAgentRun.objects.select_for_update().filter(pk=run_id,user=user).first()
            if not run:
                raise RunError('This task is unavailable.')
            if run.state.get('wp4_consent'):
                from pwanimate.services.study_context import resolve
                context=resolve(user,run.user_message.conversation)
                if context.generation!=run.state.get('wp4_binding_generation',context.generation):
                    raise RunError('The session selection changed. Review this lesson again.')
                if context.session and context.session.status!='active':
                    raise RunError('Resume the study session before deciding this lesson.')
            if run.status != 'waiting':
                raise RunError('This task has already continued. Refresh the conversation to see its status.')
            if run.expires_at <= timezone.now():
                cls.transition(run, SparkAgentRun.STATUS_EXPIRED)
                raise RunError('This approval expired. Ask Spark again to prepare a fresh action.')
            if not set(action_ids).issubset(set(run.action_ids)):
                raise RunError('An action does not belong to this task.')
            PendingAction.objects.filter(pk__in=run.action_ids,user=user,status='pending',expires_at__lte=timezone.now()).update(status='expired')
            pending = list(PendingAction.objects.select_for_update().filter(pk__in=run.action_ids,user=user,status='pending'))
            pending_ids = {str(a.id) for a in pending}
            if decision.endswith('_all') and set(action_ids) != pending_ids:
                raise RunError('The displayed actions changed. Review the current actions before approving all.')
            if not decision.endswith('_all') and len(action_ids) != 1:
                raise RunError('Approve one action, or explicitly approve all shown actions.')
            for action in pending:
                if str(action.id) not in action_ids:
                    continue
                try:
                    if decision.startswith('confirm'):
                        ActionService.confirm(user,action.id)
                    else:
                        ActionService.cancel(user,action.id)
                except ActionError:
                    # Failed/expired outcomes are retained as observations; never retry a write blindly.
                    pass
            unresolved = PendingAction.objects.filter(pk__in=run.action_ids,user=user,status='pending',expires_at__gt=timezone.now()).exists()
            if unresolved:
                return run, False
            # Lock the checkpoint before returning so another approval request cannot resume it twice.
            cls.transition(run, SparkAgentRun.STATUS_RUNNING, update_fields=['state'])
            state = run.state
            for observation in state.get('observed',[]):
                data = observation.get('result',{}).get('data')
                if isinstance(data,dict) and isinstance(data.get('action'),dict):
                    action = PendingAction.objects.filter(pk=data['action'].get('id'),user=user).first()
                    if action:
                        observation['result']['success'] = action.status in {'confirmed','cancelled'}
                        if not observation['result']['success']:
                            observation['result']['error'] = action.error or ('Action ' + action.status)
                        observation['result']['data']={'action':ActionService.serialize(action), 'approval_resolved':True,
                            'executed':action.status=='confirmed','result':action.result}
            state['items'] = [i for i in state.get('items',[]) if i.get('source') not in {'send_message','follow_user'}]
            run.state = state
            run.save(update_fields=['state','updated_at'])
            return run, True
