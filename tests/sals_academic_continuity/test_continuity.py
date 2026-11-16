from time import perf_counter
from unittest.mock import Mock, patch
from django.test import TestCase
from django.db import transaction
from pwanimate.models import (PwanimateMessage, PwanimateConversation, PwanimateStudySession,
    SparkTeachingTurn, PendingAction, SparkConceptEvidence, SparkAcademicTrackingPreference)
from pwanimate.services.unit_learning import UnitLearningService as Units
from pwanimate.services.conversation import ConversationService as Conversation
from pwanimate.services.teaching import TeachingService as Tutor
from pwanimate.services.actions import ActionService
from pwanimate.orchestrator.types import OrchestrationRequest, OrchestrationResponse
from tests.sals_03_wp1.test_mastery import setup_mastery

LINE='Explain symmetric equations of lines in 3D through P(1,2,3) with direction vector (2,-1,4)'

class ContinuityTests(TestCase):
    def setUp(self):
        self.user,self.other,_,self.enrollment,_,self.record,_,self.concept=setup_mastery()
        self.session=self.record.sessions.get()
        self.conv=self.session.conversation

    def turn(self, complete=True):
        source=Conversation.persist_user_message(self.conv,LINE)
        req=OrchestrationRequest(user=self.user,query=LINE,user_message_id=source.pk,study_mode=True)
        prepared=Tutor.prepare(req)
        response=Tutor.finish(req,prepared,Mock(),perf_counter())
        message=Conversation.persist_assistant_message(self.conv,response.answer,
            teaching_turn_id=response.metadata['teaching_turn_id'],teaching_complete=complete)
        return SparkTeachingTurn.objects.get(pk=prepared['teaching_turn_id']),message

    def test_completed_projection_and_no_mastery(self):
        turn,message=self.turn()
        turn.refresh_from_db()
        self.assertEqual(turn.completed_message_id,message.pk)
        summary=Units.summary(self.record,self.user)
        self.assertTrue(summary['teaching_progress'])
        self.assertTrue(summary['covered_topics'])
        self.assertFalse(summary['mastery_assessed'])
        self.assertTrue(summary['outstanding_checkpoints'])
        self.assertFalse(SparkConceptEvidence.objects.exists())

    def test_partial_not_finalized(self):
        turn,_=self.turn(complete=False)
        self.assertIsNone(turn.completed_at)
        self.assertEqual(Units.summary(self.record,self.user)['teaching_progress'],[])

    def test_duplicate_finalization_is_idempotent(self):
        from pwanimate.services.academic_progress import finalize
        turn,message=self.turn()
        finalize(turn.pk,message,complete=True)
        self.assertEqual(len(Units.summary(self.record,self.user)['teaching_progress']),1)

    def test_fresh_authorization_and_legacy_separation(self):
        self.assertEqual(self.record.authorized_conversation_id,self.session.conversation_id)
        self.assertEqual(Tutor.session_for(OrchestrationRequest(user=self.user,query=LINE,
            user_message_id=Conversation.persist_user_message(self.conv,LINE).pk)).pk,self.session.pk)
        Units.process_message(Conversation.persist_user_message(self.conv,LINE).pk)
        self.assertFalse(PendingAction.objects.filter(user=self.user,status='pending').exists())

    def test_new_conversation_history_and_automatic_binding(self):
        self.turn()
        Units.set_mode(self.user,'automatic')
        # Mode changes invalidate previous authorization; renew explicitly.
        self.record.refresh_from_db()
        self.record.consent_revision=Units.preference(self.user).revision
        self.record.save()
        conv=PwanimateConversation.objects.create(user=self.user)
        source=Conversation.persist_user_message(conv,LINE)
        session=Tutor.session_for(OrchestrationRequest(user=self.user,query=LINE,user_message_id=source.pk))
        self.assertEqual(session.conversation_id,conv.pk)
        self.assertNotEqual(session.pk,self.session.pk)
        self.assertTrue(Units.context_for_query(self.user,'What did we study in Linear Algebra?')[0]['teaching_progress'])

    def test_ask_requires_new_scope_and_approval_binds_current_chat(self):
        conv=PwanimateConversation.objects.create(user=self.user)
        Conversation.persist_user_message(conv,LINE)
        action=PendingAction.objects.get(user=self.user,status='pending',conversation=conv)
        ActionService.confirm(self.user,action.pk)
        session=self.record.sessions.get(conversation=conv)
        self.assertEqual(session.conversation_id,conv.pk)
        self.session.refresh_from_db()
        self.assertEqual(self.session.conversation_id,self.conv.pk)

    def test_legacy_session_attaches_only_after_explicit_consent(self):
        conv=PwanimateConversation.objects.create(user=self.user)
        legacy=PwanimateStudySession.objects.create(user=self.user,conversation=conv)
        Conversation.persist_user_message(conv,LINE)
        action=PendingAction.objects.get(user=self.user,status='pending',conversation=conv)
        ActionService.confirm(self.user,action.pk)
        legacy.refresh_from_db()
        self.assertEqual(legacy.learning_record_id,self.record.pk)
        self.assertTrue(self.record.sessions.filter(academic_conversation=conv).exists())

    def test_revocation_suppresses_model_recall(self):
        self.turn()
        Units.set_mode(self.user,'disabled')
        self.assertEqual(Units.context_for_query(self.user,'What did we study in Linear Algebra?'),[])

    def test_capture_failure_rolls_back_user_source(self):
        with patch.object(Units,'process_message',side_effect=RuntimeError('database failure')):
            with self.assertRaises(RuntimeError):
                Conversation.persist_user_message(self.conv,'Must roll back')
        self.assertFalse(self.conv.messages.filter(content='Must roll back').exists())

    def test_local_preflight_history_overhead(self):
        start=perf_counter()
        source=Conversation.persist_user_message(self.conv,LINE)
        preflight=(perf_counter()-start)*1000
        start=perf_counter()
        Units.context_for_query(self.user,'Where did we stop in MAT B201?')
        print('ACADEMIC_LOCAL_MS',{'source_and_preflight_ms':round(preflight,3),'history_ms':round((perf_counter()-start)*1000,3)})

    def test_consent_event_precedes_tokens(self):
        from rest_framework.test import APIRequestFactory, force_authenticate
        from pwanimate.api.views import PwanimateChatView
        conv=PwanimateConversation.objects.create(user=self.user)
        raw=APIRequestFactory().post('/api/pwanimate/chat/',{'message':LINE,'conversation_id':str(conv.pk)},format='json')
        force_authenticate(raw,user=self.user)
        view=PwanimateChatView()
        request=view.initialize_request(raw)
        events=[]
        def generate(req,on_progress=None,on_token=None):
            on_token('Lesson')
            return OrchestrationResponse(answer='Lesson')
        with patch.object(view,'get_orchestrator',return_value=Mock(run=generate)):
            response=view._post_chat(request,progress_callback=lambda e:events.append(e.get('type') if isinstance(e,dict) else e),token_callback=lambda t:events.append('token'))
        self.assertEqual(response.status_code,200)
        self.assertIn('academic_consent',events)
        self.assertNotIn('token',events)
        self.assertTrue(response.data['metadata']['awaiting_approval'])

    def test_ordinary_completed_answer_is_exposure_only(self):
        source=Conversation.persist_user_message(self.conv,LINE)
        Conversation.persist_assistant_message(self.conv,'A grounded lesson',academic_source_id=source.pk,academic_complete=True)
        summary=Units.summary(self.record,self.user)
        self.assertEqual(summary['teaching_progress'][0]['kind'],'topic_exposure')
        self.assertFalse(summary['mastery_assessed'])

    def test_formal_assessment_flag_uses_wp1_provenance(self):
        from pwanimate.services.concept_mastery import ConceptMasteryService as Mastery
        from pwanimate.services.assessment import AssessmentService
        from pwanimate.assessment_validation import REFERENCES
        assessment=Mastery.deliver_reference_assessment(self.user,self.concept.pk,self.session.pk,count=1)
        question=assessment.questions[0]
        AssessmentService.submit_answer(assessment,question['qid'],str(REFERENCES[question['reference_id']]['expected']))
        summary=Units.summary(self.record,self.user)
        self.assertTrue(summary['mastery_assessed'])
        self.assertEqual(summary['concept_mastery']['states']['MASTERED'],0)

    def test_interrupted_answer_cannot_be_promoted_by_cached_retry(self):
        source=Conversation.persist_user_message(self.conv,LINE)
        req=OrchestrationRequest(user=self.user,query=LINE,user_message_id=source.pk)
        prepared=Tutor.prepare(req)
        prepared['answer_markdown']=''
        model=Mock()
        model._run_tool.return_value=OrchestrationResponse(answer='Partial',finish_reason='interrupted')
        first=Tutor.finish(req,prepared,model,perf_counter())
        self.assertFalse(first.metadata['teaching_complete'])
        model._run_tool.return_value=OrchestrationResponse(answer='Full explanation',finish_reason='stop')
        second=Tutor.finish(req,prepared,model,perf_counter())
        self.assertTrue(second.metadata['teaching_complete'])
        self.assertEqual(model._run_tool.call_count,2)

    def test_ambiguous_continuity_asks_for_unit(self):
        from tests.sals_03_wp1.test_mastery import approve_record
        from documents.models import StudentUnitEnrollment
        other=StudentUnitEnrollment.objects.filter(user=self.user).exclude(pk=self.enrollment.pk).get()
        approve_record(self.user,other)
        self.assertTrue(Units.context_for_query(self.user,'Continue our previous lesson.')[0]['clarification_required'])

    def test_model_prompt_contains_real_history(self):
        from pwanimate.context.types import ContextPackage
        self.turn()
        history=Units.context_for_query(self.user,'What did we study in Linear Algebra?')
        package=ContextPackage(query='history',academic_unit_context=history)
        prompt=package.format_context_text()
        self.assertIn('teaching_progress',prompt)
        self.assertIn('explanation_completed',prompt)

    def test_continuation_creates_new_run_without_stealing_old(self):
        previous,_=self.turn()
        old_run=previous.run
        Units.set_mode(self.user,'automatic')
        self.record.refresh_from_db()
        self.record.consent_revision=Units.preference(self.user).revision
        self.record.save()
        conv=PwanimateConversation.objects.create(user=self.user)
        source=Conversation.persist_user_message(conv,'Continue our previous lesson.')
        req=OrchestrationRequest(user=self.user,query=source.content,user_message_id=source.pk)
        prepared=Tutor.prepare(req)
        self.assertNotEqual(prepared.get('run_id'),str(old_run.pk))
        old_run.refresh_from_db()
        self.assertEqual(old_run.status,'waiting')
        self.assertEqual(prepared['teaching']['phase'],'DEMONSTRATE')


    def test_formative_attempt_and_assignment_are_distinct(self):
        from pwanimate.services.agent_execution import claim_run
        from pwanimate.models import SparkAgentInteraction, SparkTeachingState
        turn,_=self.turn()
        source=PwanimateMessage.objects.get(pk=int(turn.receipt_key.split(':')[1]))
        req=OrchestrationRequest(user=self.user,query=LINE,user_message_id=source.pk)
        for value in ['continue','continue','ANSWER']:
            state=SparkTeachingState.objects.get(session=self.session)
            interaction=SparkAgentInteraction.objects.get(run=state.active_run,status='pending')
            answer={'text':str(state.exercise['expected'])} if value=='ANSWER' else {'selected':[value]}
            run,_=claim_run(self.user,state.active_run,interaction=interaction,raw_answer=answer)
            prepared=Tutor.prepare(req,run=run)
            response=Tutor.finish(req,prepared,Mock(),perf_counter())
            Conversation.persist_assistant_message(self.conv,response.answer,
                teaching_turn_id=response.metadata['teaching_turn_id'],teaching_complete=response.metadata['teaching_complete'])
        progress=Units.summary(self.record,self.user)['teaching_progress']
        self.assertTrue(any(p['phase']=='PRACTICE' for p in progress))
        attempts=[p for p in progress if p['kind']=='independent_attempt']
        self.assertEqual(len(attempts),1)
        self.assertTrue(attempts[0]['evaluation']['correct'])
        self.assertFalse(Units.summary(self.record,self.user)['mastery_assessed'])

    def test_owner_isolation_and_preconsent_exclusion(self):
        from pwanimate.services.academic_progress import finalize
        turn,message=self.turn()
        otherconv=PwanimateConversation.objects.create(user=self.other)
        foreign=PwanimateMessage.objects.create(conversation=otherconv,role='assistant',content='Wrong owner')
        with self.assertRaises(ValueError):
            finalize(turn.pk,foreign,complete=True)
        self.assertEqual(Units.context_for_query(self.other,'What did we study in Linear Algebra?'),[])
        conv=PwanimateConversation.objects.create(user=self.user)
        source=Conversation.persist_user_message(conv,LINE)
        Conversation.persist_assistant_message(conv,'Untracked answer',academic_source_id=source.pk,academic_complete=True)
        self.assertFalse(SparkTeachingTurn.objects.filter(completed_message__conversation=conv).exists())


    def test_unknown_fresh_continuation_requests_unit_without_model_call(self):
        from rest_framework.test import APIClient
        from pwanimate.api.views import PwanimateChatView
        client=APIClient()
        client.force_authenticate(self.other)
        orchestrator=Mock()
        with patch.object(PwanimateChatView,'get_orchestrator',return_value=orchestrator):
            result=client.post('/api/pwanimate/chat/',{'message':'Continue our previous lesson.'},format='json')
        self.assertEqual(result.status_code,200)
        self.assertIn('Which unit and semester',result.data['answer'])
        orchestrator.run.assert_not_called()


from django.test import TransactionTestCase, override_settings
from django.db import connection, close_old_connections
from django.db.migrations.loader import MigrationLoader
from unittest import skipUnless
import threading

class CompletionMigrationTests(TransactionTestCase):
    def test_real_additive_migration(self):
        from pwanimate.models import SparkTeachingTurn, PwanimateStudySession
        with override_settings(MIGRATION_MODULES={}):
            loader=MigrationLoader(None)
        with connection.schema_editor() as editor:
            editor.remove_field(SparkTeachingTurn,SparkTeachingTurn._meta.get_field('completed_message'))
            editor.remove_field(SparkTeachingTurn,SparkTeachingTurn._meta.get_field('completed_at'))
            editor.remove_field(PwanimateStudySession,PwanimateStudySession._meta.get_field('academic_conversation'))
        state=loader.project_state([('pwanimate','0036_memory_dispatch_outbox')])
        with connection.schema_editor() as editor:
            loader.get_migration('pwanimate','0037_academic_completion').apply(state,editor)
        self.assertEqual(SparkTeachingTurn.objects.count(),0)

@skipUnless(connection.vendor=='postgresql','Owner-run PostgreSQL completion race')
class CompletionConcurrencyTests(TransactionTestCase):
    def test_duplicate_finalization(self):
        from pwanimate.services.academic_progress import finalize
        user,_,_,_,_,record,_,_=setup_mastery()
        session=record.sessions.get()
        source=Conversation.persist_user_message(session.conversation,LINE)
        req=OrchestrationRequest(user=user,query=LINE,user_message_id=source.pk)
        prepared=Tutor.prepare(req)
        message=PwanimateMessage.objects.create(conversation=session.conversation,role='assistant',content='Completed explanation')
        errors=[]
        barrier=threading.Barrier(2)
        def work():
            close_old_connections()
            try:
                barrier.wait(timeout=10)
                finalize(prepared['teaching_turn_id'],message,complete=True)
            except Exception as exc:
                errors.append(exc)
            finally:
                close_old_connections()
        workers=[threading.Thread(target=work) for _ in range(2)]
        for worker in workers: worker.start()
        for worker in workers: worker.join(15)
        self.assertFalse(any(worker.is_alive() for worker in workers))
        self.assertFalse(errors)
        self.assertEqual(SparkTeachingTurn.objects.filter(completed_message=message).count(),1)
