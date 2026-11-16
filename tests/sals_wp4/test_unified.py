from unittest.mock import Mock,patch
from time import perf_counter
from django.test import TestCase
from rest_framework.test import APIClient,APIRequestFactory,force_authenticate
from pwanimate.models import *
from pwanimate.services.study_context import resolve,select,StudyConflict
from pwanimate.services.study_session import StudySessionService as Study
from pwanimate.services.teaching import TeachingService as Tutor
from pwanimate.services.conversation import ConversationService as Conversation
from pwanimate.services.unit_learning import UnitLearningService as Units
from pwanimate.services.agent_execution import execute_claimed,claim_run
from pwanimate.services.agent_runs import AgentRunService
from pwanimate.orchestrator.types import OrchestrationRequest,OrchestrationResponse
from tests.sals_03_wp1.test_mastery import setup_mastery
from tests.sals_academic_continuity.test_continuity import LINE

class UnifiedTests(TestCase):
    def setUp(self):
        self.user,self.other,_,_,_,self.record,_,_=setup_mastery()
        self.session=self.record.sessions.get()
        self.conv=self.session.conversation
        self.client=APIClient();self.client.force_authenticate(self.user)

    def test_collision_handoff_pause_and_stale_generation(self):
        conv=PwanimateConversation.objects.create(user=self.user)
        legacy=PwanimateStudySession.objects.create(user=self.user,conversation=conv)
        self.session.academic_conversation=conv;self.session.save()
        context=resolve(self.user,conv)
        self.assertIsNone(context.session)
        self.assertEqual(len(context.candidates),2)
        with self.assertRaises(StudyConflict):Tutor.session_for(OrchestrationRequest(user=self.user,query=LINE,user_message_id=PwanimateMessage.objects.create(conversation=conv,role='user',content=LINE).pk))
        context=select(self.user,conv,self.session.pk,0)
        self.assertEqual(Study.get_session_for_conversation(self.user,conv).pk,self.session.pk)
        legacy.refresh_from_db();self.assertEqual(legacy.status,'paused')
        with self.assertRaises(StudyConflict):select(self.user,conv,legacy.pk,0)
        Study.pause_session(self.user,self.session.pk)
        self.assertEqual(resolve(self.user,conv).session.status,'paused')
        self.assertEqual(Units._session(self.user,self.record,conversation=conv).pk,self.session.pk)

    def test_pause_fences_question_and_resume_preserves_id(self):
        source=Conversation.persist_user_message(self.conv,LINE)
        req=OrchestrationRequest(user=self.user,query=LINE,user_message_id=source.pk)
        prepared=Tutor.prepare(req)
        state=SparkTeachingState.objects.get(session=self.session)
        interaction=SparkAgentInteraction.objects.get(run=state.active_run,status='pending')
        Study.pause_session(self.user,self.session.pk)
        with self.assertRaises(StudyConflict):claim_run(self.user,state.active_run,interaction=interaction,raw_answer={'selected':['continue']})
        interaction.refresh_from_db();self.assertEqual(interaction.status,'pending')
        Study.resume_session(self.user,self.session.pk)
        run,_=claim_run(self.user,state.active_run,interaction=interaction,raw_answer={'selected':['continue']})
        again=Tutor.prepare(req,run=run)
        self.assertEqual(again['run_id'],prepared['run_id'])

    def test_handoff_parks_question_without_blocking_new_task(self):
        from pwanimate.api.views import PwanimateChatView
        source=Conversation.persist_user_message(self.conv,LINE)
        Tutor.prepare(OrchestrationRequest(user=self.user,query=LINE,user_message_id=source.pk))
        state=SparkTeachingState.objects.get(session=self.session)
        question=SparkAgentInteraction.objects.get(run=state.active_run,status='pending')
        home=PwanimateConversation.objects.create(user=self.user)
        legacy=PwanimateStudySession.objects.create(user=self.user,conversation=home,academic_conversation=self.conv)
        select(self.user,self.conv,legacy.pk,0)
        self.assertIsNone(resolve(self.user,self.conv).payload()['pending_run_id'])
        raw=APIRequestFactory().post('/api/pwanimate/chat/',{'message':LINE,'conversation_id':str(self.conv.pk)},format='json')
        force_authenticate(raw,user=self.user)
        view=PwanimateChatView()
        with patch.object(view,'get_orchestrator',return_value=Mock()):
            result=view._post_chat(view.initialize_request(raw))
        self.assertEqual(result.status_code,200,result.data)
        question.refresh_from_db();self.assertEqual(question.status,'pending')
        self.session.refresh_from_db();self.assertEqual(self.session.status,'paused')

    def test_ended_does_not_reopen(self):
        Study.end_session(self.user,self.session.pk)
        with self.assertRaises(ValueError):Study.resume_session(self.user,self.session.pk)

    def test_owner_and_generation_api(self):
        url=f'/api/pwanimate/study-context/{self.conv.pk}/'
        self.assertEqual(self.client.get(url).status_code,200)
        self.assertEqual(self.client.post(url,{'session_id':str(self.session.pk),'generation':99},format='json').status_code,409)
        self.client.force_authenticate(self.other)
        self.assertEqual(self.client.get(url).status_code,404)

    def test_consent_holds_then_original_run_executes_after_approval(self):
        from pwanimate.api.views import PwanimateChatView
        conv=PwanimateConversation.objects.create(user=self.user)
        raw=APIRequestFactory().post('/api/pwanimate/chat/',{'message':LINE,'conversation_id':str(conv.pk)},format='json')
        force_authenticate(raw,user=self.user)
        view=PwanimateChatView();request=view.initialize_request(raw)
        with patch.object(view,'get_orchestrator',return_value=Mock()) as factory:
            result=view._post_chat(request)
        factory.return_value.run.assert_not_called()
        run=SparkAgentRun.objects.get(user_message__conversation=conv)
        self.assertEqual(run.status,'waiting')
        action=PendingAction.objects.get(pk=run.action_ids[0])
        AgentRunService.decide(self.user,run.pk,'confirm',[str(action.pk)])
        run.refresh_from_db()
        self.assertEqual(run.status,'running')
        # Real deterministic controller/finish path; no provider invocation.
        from pwanimate.orchestrator.service import PwanimateOrchestrator
        orchestrator=Mock()
        orchestrator._request_metadata.return_value={}
        orchestrator.finish_agent=lambda req,prepared,start,*args: Tutor.finish(req,prepared,orchestrator,start)
        with patch.object(PwanimateChatView,'get_orchestrator',return_value=orchestrator):
            payload=execute_claimed(run)
        run.refresh_from_db()
        self.assertEqual(str(run.pk),payload['run_id'])
        boundary=PwanimateMessage.objects.get(pk=run.snapshot['execution_boundary_id'])
        action.refresh_from_db()
        record=SparkUnitLearningRecord.objects.get(pk=action.result['record_id'])
        self.assertGreater(boundary.created_at,record.authorized_at)
        self.assertTrue(SparkTeachingTurn.objects.filter(state__session__learning_record=record,completed_at__isnull=False).exists())
        self.assertFalse(SparkConceptEvidence.objects.filter(concept__record=record).exists())

    def test_decline_executes_untracked_and_boundary_is_hidden(self):
        conv=PwanimateConversation.objects.create(user=self.user)
        source=Conversation.persist_user_message(conv,LINE)
        req=OrchestrationRequest(user=self.user,query=LINE,user_message_id=source.pk)
        action=PendingAction.objects.get(conversation=conv,status='pending')
        from pwanimate.services.actions import ActionService
        run=AgentRunService.pause(req,{'wp4_consent':True},[ActionService.serialize(action)])
        AgentRunService.decide(self.user,run.pk,'cancel',[str(action.pk)])
        run.refresh_from_db()
        from pwanimate.api.views import PwanimateChatView
        orchestrator=Mock();orchestrator._request_metadata.return_value={}
        orchestrator.finish_agent.return_value=OrchestrationResponse(answer='Untracked tutoring')
        with patch('pwanimate.orchestrator.agent.StudyAgent.run',return_value={'items':[]}),patch.object(PwanimateChatView,'get_orchestrator',return_value=orchestrator):
            result=execute_claimed(run)
        self.assertTrue(result['message_id'])
        self.assertFalse(SparkUnitActivity.objects.filter(source_conversation=conv).exists())
        self.assertFalse(SparkTeachingTurn.objects.filter(completed_message__conversation=conv).exists())
        self.assertEqual(conv.messages.filter(task='academic_execution').count(),1)
        # Test the shared history loader: the internal receipt is not dialogue.
        history=Conversation.load_history(conv)
        self.assertEqual(sum(m.content==LINE for m in history),1)

    def test_document_unit_page_and_completed_turn(self):
        from documents.models import DocumentAcademicUnit,Category
        from pwanimate.tests.test_document_context import DocumentContextResolutionTests as Fixtures
        from pwanimate.ai.embeddings.mock import MockEmbeddingProvider
        from pwanimate.orchestrator.document_context import DocumentTurnContext
        self.category=Category.objects.create(name='WP4 book',code='wp4-book')
        self.emb=MockEmbeddingProvider()
        document=Fixtures._make_doc(self,'WP4 textbook',self.user)
        version=Fixtures._make_version(self,document,1)
        Fixtures._make_file(self,version,page_count=5)
        Fixtures._make_chunk(self,document,version,0,'A matrix is a rectangular array.',page=2)
        DocumentAcademicUnit.objects.create(document=document,academic_unit=self.record.academic_unit,semester=self.record.semester)
        resources=[{'type':'document','documentId':str(document.pk),'pageNumber':2}]
        source=Conversation.persist_user_message(self.conv,'Explain this page')
        Units.process_message(source.pk,document_id=document.pk)
        req=OrchestrationRequest(user=self.user,query=source.content,user_message_id=source.pk,context_resources=resources,study_mode=True)
        req.document_context=DocumentTurnContext.resolve(self.user,resources,req.query)
        self.assertTrue(Tutor.applies(req))
        prepared=Tutor.prepare(req)
        response=OrchestrationResponse(answer='Source-grounded explanation',finish_reason='stop',sources=[{'document_id':document.pk,'page_number':2}])
        orchestrator=Mock();orchestrator._run_tool.return_value=response
        response=Tutor.finish(req,prepared,orchestrator,perf_counter())
        Conversation.persist_assistant_message(self.conv,response.answer,sources=response.sources,
            teaching_turn_id=response.metadata['teaching_turn_id'],teaching_complete=response.metadata['teaching_complete'])
        progress=Units.summary(self.record,self.user)['teaching_progress']
        self.assertEqual(progress[-1]['sources'][0]['page_number'],2)
        replay=Tutor.finish(req,prepared,orchestrator,perf_counter())
        self.assertEqual(replay.sources[0]['page_number'],2)
        self.assertFalse(Units.summary(self.record,self.user)['mastery_assessed'])

    def test_revocation_and_pause_prevent_finalization(self):
        from pwanimate.services.academic_progress import finalize
        source=Conversation.persist_user_message(self.conv,LINE)
        prepared=Tutor.prepare(OrchestrationRequest(user=self.user,query=LINE,user_message_id=source.pk))
        turn=SparkTeachingTurn.objects.get(pk=prepared['teaching_turn_id'])
        message=PwanimateMessage.objects.create(conversation=self.conv,role='assistant',content='Delayed response')
        Study.pause_session(self.user,self.session.pk)
        finalize(turn.pk,message,complete=True)
        turn.refresh_from_db();self.assertIsNone(turn.completed_at)
        Study.resume_session(self.user,self.session.pk)
        Units.set_mode(self.user,'disabled')
        finalize(turn.pk,message,complete=True)
        turn.refresh_from_db();self.assertIsNone(turn.completed_at)

    def test_readonly_resolution_never_reactivates_or_duplicates(self):
        Study.pause_session(self.user,self.session.pk)
        before=PwanimateStudySession.objects.filter(user=self.user).count()
        for _ in range(3):
            context=resolve(self.user,self.conv)
            self.assertEqual(context.session.status,'paused')
            self.assertEqual(Units._session(self.user,self.record,conversation=self.conv).pk,self.session.pk)
        self.assertEqual(PwanimateStudySession.objects.filter(user=self.user).count(),before)

    def test_renew_consent_preserves_original_question(self):
        from pwanimate.services.study_context import request_tracking
        from pwanimate.services.actions import ActionService
        source=Conversation.persist_user_message(self.conv,LINE)
        prepared=Tutor.prepare(OrchestrationRequest(user=self.user,query=LINE,user_message_id=source.pk))
        old_question=SparkAgentInteraction.objects.get(run_id=prepared['run_id'],status='pending')
        Units.set_mode(self.user,'ask')
        actions=request_tracking(self.user,self.conv,0)
        self.assertEqual(len(actions),1)
        ActionService.confirm(self.user,actions[0]['id'])
        old_question.refresh_from_db()
        self.assertEqual(old_question.status,'pending')
        self.assertEqual(resolve(self.user,self.conv).tracking,'authorized')

    def test_unsourced_document_explanation_is_not_completed(self):
        source=Conversation.persist_user_message(self.conv,LINE)
        req=OrchestrationRequest(user=self.user,query=LINE,user_message_id=source.pk)
        req.document_context=Mock(resolutions=[Mock(resolved=True)])
        prepared=Tutor.prepare(req)
        response=Tutor.finish(req,prepared,Mock(),perf_counter())
        self.assertFalse(response.metadata['teaching_complete'])
        Conversation.persist_assistant_message(self.conv,response.answer,
            teaching_turn_id=response.metadata['teaching_turn_id'],teaching_complete=response.metadata['teaching_complete'])
        self.assertFalse(SparkTeachingTurn.objects.filter(completed_at__isnull=False,state__session=self.session).exists())


from django.test import TransactionTestCase,override_settings
from django.db import connection,close_old_connections
from django.db.migrations.loader import MigrationLoader
from unittest import skipUnless
import threading

class CanonicalMigrationTests(TransactionTestCase):
    def test_actual_additive_binding_migration_preserves_sessions(self):
        user,_,_,_,_,record,_,_=setup_mastery()
        session=record.sessions.get()
        with override_settings(MIGRATION_MODULES={}):loader=MigrationLoader(None)
        with connection.schema_editor() as editor:
            editor.remove_field(PwanimateConversation,PwanimateConversation._meta.get_field('canonical_study_session'))
            editor.remove_field(PwanimateConversation,PwanimateConversation._meta.get_field('study_binding_generation'))
        state=loader.project_state([('pwanimate','0037_academic_completion')])
        with connection.schema_editor() as editor:
            loader.get_migration('pwanimate','0038_canonical_study_binding').apply(state,editor)
        conv=PwanimateConversation.objects.get(pk=session.conversation_id)
        self.assertIsNone(conv.canonical_study_session_id)
        self.assertEqual(conv.study_binding_generation,0)
        self.assertEqual(record.sessions.get().pk,session.pk)

@skipUnless(connection.vendor=='postgresql','Owner-run PostgreSQL canonical selection race')
class CanonicalConcurrencyTests(TransactionTestCase):
    def test_only_one_handoff_generation_wins(self):
        user,_,_,_,_,record,_,_=setup_mastery()
        bound=record.sessions.get()
        conv=PwanimateConversation.objects.create(user=user)
        legacy=PwanimateStudySession.objects.create(user=user,conversation=conv)
        bound.academic_conversation=conv;bound.save()
        barrier=threading.Barrier(2);results=[];errors=[]
        def work(session_id):
            close_old_connections()
            try:
                barrier.wait(timeout=10)
                select(user,conv,session_id,0)
                results.append('selected')
            except StudyConflict:
                results.append('stale')
            except Exception as exc:errors.append(exc)
            finally:close_old_connections()
        workers=[threading.Thread(target=work,args=(s.pk,)) for s in [legacy,bound]]
        for worker in workers:worker.start()
        for worker in workers:worker.join(15)
        self.assertFalse(any(worker.is_alive() for worker in workers))
        self.assertFalse(errors)
        self.assertCountEqual(results,['selected','stale'])
        conv.refresh_from_db();self.assertEqual(conv.study_binding_generation,1)
