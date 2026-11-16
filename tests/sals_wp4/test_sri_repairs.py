"""SRI-01 boundary regressions. Synthetic data and mocked generation only."""
from unittest.mock import Mock, patch
from django.test import TestCase
from rest_framework.test import APIRequestFactory, force_authenticate
from pwanimate.models import (PwanimateConversation, PwanimateMessage, PendingAction,
    SparkAgentRun, SparkUnitActivity, SparkTeachingTurn, SparkConceptEvidence)
from pwanimate.services.agent_runs import AgentRunService, RunError
from pwanimate.services.agent_execution import execute_claimed
from pwanimate.services.execution_source import effective_execution_source
from pwanimate.services.study_context import resolve
from pwanimate.services.study_session import StudySessionService
from pwanimate.services.unit_learning import UnitLearningService as Units
from pwanimate.services.teaching import TeachingService
from pwanimate.api.views import PwanimateChatView
from pwanimate.orchestrator.types import OrchestrationResponse
from tests.sals_03_wp1.test_mastery import setup_mastery


class ExecutionSourceTests(TestCase):
    def setUp(self):
        self.user, self.other, _, _, _, self.record, _, _ = setup_mastery()

    def held_run(self, resources=None, query=None):
        conv = PwanimateConversation.objects.create(user=self.user)
        query = query or 'What is a vector space in ' + self.record.academic_unit.name + '?'
        raw = APIRequestFactory().post('/api/pwanimate/chat/',
            {'message': query, 'conversation_id': str(conv.pk), 'context_resources': resources or []}, format='json')
        force_authenticate(raw, user=self.user)
        view = PwanimateChatView()
        with patch.object(view, 'get_orchestrator', return_value=Mock()):
            view._post_chat(view.initialize_request(raw))
        run = SparkAgentRun.objects.get(user_message__conversation=conv)
        action = PendingAction.objects.get(pk=run.action_ids[0])
        AgentRunService.decide(self.user, run.pk, 'confirm', [str(action.pk)])
        run.refresh_from_db()
        return run

    def execute(self, run, during_generation=None):
        orchestrator = Mock()
        orchestrator._request_metadata.return_value = {}
        def finish(*args):
            if during_generation:
                during_generation()
            return OrchestrationResponse(answer='A completed ordinary explanation.', finish_reason='stop')
        orchestrator.finish_agent.side_effect = finish
        with patch('pwanimate.orchestrator.agent.StudyAgent.run', return_value={'items': []}), \
             patch.object(PwanimateChatView, 'get_orchestrator', return_value=orchestrator):
            return execute_claimed(run)

    def test_question_form_uses_prospective_source_once_without_mastery(self):
        run = self.held_run()
        payload = self.execute(run)
        run.refresh_from_db()
        boundary = effective_execution_source(run)
        self.assertNotEqual(boundary.pk, run.user_message_id)
        self.assertFalse(SparkUnitActivity.objects.filter(source_message_id=run.user_message_id).exists())
        activity = SparkUnitActivity.objects.get(source_message=boundary)
        turn = SparkTeachingTurn.objects.get(completed_message_id=payload['message_id'])
        self.assertEqual(turn.receipt_key, 'message:' + str(boundary.pk))
        self.assertEqual(turn.activity_kind, 'topic_exposure')
        self.assertEqual(turn.state.session_id, activity.session_id)
        self.assertEqual(activity.record.semester_id, self.record.semester_id)
        self.assertFalse(SparkConceptEvidence.objects.filter(concept__record=activity.record).exists())
        from pwanimate.services.academic_progress import finalize_ordinary
        finalize_ordinary(boundary.pk, turn.completed_message, complete=True)
        self.assertEqual(SparkTeachingTurn.objects.filter(state__session=activity.session).count(), 1)

    def test_pause_or_revoke_during_generation_prevents_capture(self):
        for operation in ('pause', 'revoke'):
            with self.subTest(operation=operation):
                if operation == 'revoke':
                    Units.set_mode(self.user, 'ask')
                run = self.held_run()
                session = resolve(self.user, run.user_message.conversation).session
                def stop():
                    if operation == 'pause':
                        StudySessionService.pause_session(self.user, session.pk)
                    else:
                        Units.set_mode(self.user, 'disabled')
                payload = self.execute(run, stop)
                self.assertFalse(SparkTeachingTurn.objects.filter(completed_message_id=payload['message_id']).exists())

    def test_foreign_boundary_is_rejected(self):
        run = self.held_run()
        foreign = PwanimateMessage.objects.create(
            conversation=PwanimateConversation.objects.create(user=self.other),
            role='user', task='academic_execution', content='foreign')
        run.snapshot['execution_boundary_id'] = foreign.pk
        with self.assertRaises(RunError):
            effective_execution_source(run)

    def test_document_mapped_boundary_records_only_exposure(self):
        from documents.models import DocumentAcademicUnit, Category
        from pwanimate.tests.test_document_context import DocumentContextResolutionTests as Fixtures
        from pwanimate.ai.embeddings.mock import MockEmbeddingProvider
        self.category = Category.objects.create(name='SRI book', code='sri-book')
        self.emb = MockEmbeddingProvider()
        document = Fixtures._make_doc(self, 'SRI textbook', self.user)
        version = Fixtures._make_version(self, document, 1)
        Fixtures._make_file(self, version, page_count=5)
        Fixtures._make_chunk(self, document, version, 0, 'Vector space definitions.', page=2)
        DocumentAcademicUnit.objects.create(document=document, academic_unit=self.record.academic_unit,
            semester=self.record.semester)
        run = self.held_run([{'type':'document','documentId':str(document.pk),'pageNumber':2}],
            query='What is the definition on this page?')
        payload = self.execute(run)
        run.refresh_from_db()
        activity = SparkUnitActivity.objects.get(source_message_id=run.snapshot['execution_boundary_id'])
        turn = SparkTeachingTurn.objects.get(completed_message_id=payload['message_id'])
        self.assertEqual(turn.state.session_id, activity.session_id)
        self.assertEqual(turn.activity_kind, 'topic_exposure')
        self.assertFalse(SparkConceptEvidence.objects.filter(concept__record=activity.record).exists())


class CompletionContractTests(TestCase):
    def test_only_explicit_stop_completes(self):
        from pwanimate.ai.completion import is_complete, normalize_finish_reason, needs_continuation
        for raw in ('length','max_tokens','MAX_TOKENS','interrupted','content_filter','error',None,''):
            with self.subTest(reason=raw):
                self.assertFalse(is_complete(raw, 'partial'))
        self.assertTrue(is_complete('STOP', 'done'))
        self.assertEqual(normalize_finish_reason('MAX_TOKENS'), 'length')
        self.assertTrue(needs_continuation('max_tokens', 'partial'))
        self.assertFalse(needs_continuation(None, 'partial'))

    def test_partial_response_marks_run_interrupted_and_never_captures(self):
        case = ExecutionSourceTests()
        case.user, case.other, _, _, _, case.record, _, _ = setup_mastery()
        run = case.held_run()
        orchestrator = Mock()
        orchestrator._request_metadata.return_value = {}
        orchestrator.finish_agent.return_value = OrchestrationResponse(answer='partial code =', finish_reason='max_tokens')
        with patch('pwanimate.orchestrator.agent.StudyAgent.run', return_value={'items': []}), \
             patch.object(PwanimateChatView, 'get_orchestrator', return_value=orchestrator):
            payload = execute_claimed(run)
        run.refresh_from_db()
        self.assertEqual(run.status, 'interrupted')
        self.assertFalse(payload['response_complete'])
        self.assertFalse(SparkTeachingTurn.objects.filter(completed_message_id=payload['message_id']).exists())

    def test_continuation_is_bounded_and_terminal_reason_is_preserved(self):
        from pwanimate.orchestrator.service import PwanimateOrchestrator
        from pwanimate.ai.gateway.types import LLMRequest, LLMResponse
        for final_reason in ('stop', 'MAX_TOKENS'):
            gateway = Mock()
            gateway.generate.side_effect = [
                LLMResponse(content='part ', provider='mock', model='mock', finish_reason='max_tokens'),
                LLMResponse(content='end', provider='mock', model='mock', finish_reason=final_reason)]
            response = PwanimateOrchestrator._generate(
                type('Generator', (), {'gateway': gateway,
                    '_needs_continuation': staticmethod(PwanimateOrchestrator._needs_continuation),
                    '_continuation_request': staticmethod(PwanimateOrchestrator._continuation_request),
                    '_generate': PwanimateOrchestrator._generate})(), LLMRequest())
            self.assertEqual(gateway.generate.call_count, 2)
            self.assertEqual(response.content, 'part end')
            self.assertEqual(response.finish_reason, 'stop' if final_reason == 'stop' else 'length')


class TerminalEvidenceTests(TestCase):
    def test_stream_eof_is_unknown_for_all_native_adapters(self):
        from pwanimate.tests.test_streaming import FakeStreamResponse, _stream_request, _sse_lines
        from pwanimate.ai.providers.groq import GroqLLMProvider
        from pwanimate.ai.providers.openrouter import OpenRouterLLMProvider
        from pwanimate.ai.providers.gemini import GeminiLLMProvider
        for cls in (GroqLLMProvider, OpenRouterLLMProvider, GeminiLLMProvider):
            provider = cls(api_key='offline-test')
            chunk = ({'candidates':[{'content':{'parts':[{'text':'partial'}]}}]}
                if cls is GeminiLLMProvider else {'choices':[{'delta':{'content':'partial'}}]})
            with patch.object(provider, 'session') as session:
                session.post.return_value = FakeStreamResponse(_sse_lines(chunk))
                response = provider.generate_stream(_stream_request(), lambda text: None)
            self.assertEqual(response.finish_reason, 'unknown')
            self.assertFalse(response.metadata['terminal_finish_received'])

    def test_worker_error_payload_never_claims_application_success(self):
        from pwanimate.tasks.agent_runs import execute_agent_run
        case = ExecutionSourceTests()
        case.user, case.other, _, _, _, case.record, _, _ = setup_mastery()
        run = case.held_run()
        run.attempt_id = 'offline-attempt'
        run.save(update_fields=['attempt_id'])
        with patch('pwanimate.services.agent_execution.execute_claimed', side_effect=RuntimeError('offline')):
            payload = execute_agent_run.run(str(run.pk), run.attempt_id)
        run.refresh_from_db()
        self.assertEqual(run.status, 'failed')
        self.assertEqual(payload['status'], 'error')
        self.assertFalse(payload['application_success'])

    def test_mark_failed_failure_still_returns_error_and_never_replays(self):
        from pwanimate.tasks.agent_runs import execute_agent_run
        case = ExecutionSourceTests()
        case.user, case.other, _, _, _, case.record, _, _ = setup_mastery()
        run = case.held_run()
        run.attempt_id = 'offline-attempt'
        run.save(update_fields=['attempt_id'])
        with patch('pwanimate.services.agent_execution.execute_claimed', side_effect=RuntimeError('offline')), \
             patch('pwanimate.services.agent_execution.mark_failed', side_effect=RuntimeError('database unavailable')):
            payload = execute_agent_run.run(str(run.pk), run.attempt_id)
            duplicate = execute_agent_run.run(str(run.pk), run.attempt_id)
        self.assertFalse(payload['application_success'])
        self.assertEqual(duplicate['status'], 'duplicate')

    def test_expired_attempt_keeps_result_pointer_without_replay(self):
        from django.utils import timezone
        from pwanimate.services.agent_recovery import recover_stale_runs
        case = ExecutionSourceTests()
        case.user, case.other, _, _, _, case.record, _, _ = setup_mastery()
        run = case.held_run()
        message = PwanimateMessage.objects.create(conversation=run.user_message.conversation,
            role='assistant', content='Partial result')
        run.result_message = message
        run.lease_expires_at = timezone.now() - timezone.timedelta(seconds=1)
        run.save(update_fields=['result_message','lease_expires_at'])
        with patch('pwanimate.tasks.agent_runs.dispatch_run') as dispatch:
            recover_stale_runs()
        run.refresh_from_db()
        self.assertEqual(run.status, 'interrupted')
        self.assertEqual(run.result_message_id, message.pk)
        dispatch.assert_not_called()


from django.test import TransactionTestCase

class TeachingTransportTests(TransactionTestCase):
    def setUp(self):
        # Native asyncio executor shutdown hangs in the restricted runner. Keep
        # real HTTP/SSE serialization, scheduling disposable ORM reads inline.
        async def inline(function, *args, **kwargs):
            with patch('django.utils.asyncio.get_running_loop', side_effect=RuntimeError):
                return function(*args, **kwargs)
        patcher = patch('pwanimate.api.agent_runs.asyncio.to_thread', new=inline)
        patcher.start()
        self.addCleanup(patcher.stop)
        # Keep a failed stream assertion bounded rather than waiting ten minutes.
        patcher = patch('pwanimate.api.agent_runs.TAIL_MAX_SECONDS', 2)
        patcher.start()
        self.addCleanup(patcher.stop)
        patcher = patch('pwanimate.api.agent_runs.TAIL_POLL_SECONDS', 0)
        patcher.start()
        self.addCleanup(patcher.stop)
        # Commit real HTTP results without publishing unrelated background jobs.
        for target in ('pwanimate.services.conversation.ConversationService.schedule_embedding',
                       'pwanimate.services.conversation.ConversationService.schedule_digest',
                       'pwanimate.services.memory_dispatch.wake_dispatch'):
            patcher = patch(target)
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_http_sse_consent_repause_reconnect_and_history_preserve_segment(self):
        import asyncio
        import json
        from rest_framework.test import APIClient
        from tests.sals_academic_continuity.test_continuity import LINE
        from pwanimate.api.agent_runs import _terminal_payload
        from pwanimate.api.serializers import MessageSerializer
        user, _, _, _, _, _, _, _ = setup_mastery()
        conv = PwanimateConversation.objects.create(user=user)
        raw = APIRequestFactory().post('/api/pwanimate/chat/',
            {'message': LINE, 'conversation_id': str(conv.pk)}, format='json')
        force_authenticate(raw, user=user)
        view = PwanimateChatView()
        with patch.object(view, 'get_orchestrator', return_value=Mock()):
            view._post_chat(view.initialize_request(raw))
        run = SparkAgentRun.objects.get(user_message__conversation=conv)
        orchestrator = Mock()
        orchestrator._request_metadata.return_value = {}
        def finish(req, prepared, start, *args):
            response = TeachingService.finish(req, prepared, orchestrator, start)
            response.sources = [{'document_id':'offline-source','page_number':2}]
            response.citations = ['offline-source p2']
            return response
        orchestrator.finish_agent.side_effect = finish
        dispatched_payloads = []
        def dispatch(run_id, attempt):
            dispatched_payloads.append(execute_claimed(SparkAgentRun.objects.get(pk=run_id)))
        client = APIClient()
        client.force_authenticate(user)
        with patch.object(PwanimateChatView, 'get_orchestrator', return_value=orchestrator), \
             patch('pwanimate.tasks.agent_runs.dispatch_run', side_effect=dispatch):
            response = client.post('/api/pwanimate/chat/',
                {'resume_run_id':str(run.pk),'decision':'confirm','action_ids':run.action_ids},
                format='json', HTTP_ACCEPT='text/event-stream')
        run.refresh_from_db()
        self.assertIsNotNone(run.result_message_id)
        self.assertEqual(run.status, 'waiting')
        async def collect(response):
            chunks = []
            iterator = response.streaming_content.__aiter__()
            for _ in range(100):
                part = await asyncio.wait_for(anext(iterator), timeout=3)
                chunks.append(part)
                if b'event: response' in part:
                    return b''.join(chunks).decode()
            self.fail('No terminal response in bounded SSE replay')
        body = asyncio.run(collect(response))
        terminal = json.loads(body.split('event: response\ndata: ')[-1].strip())['data']
        run.refresh_from_db()
        self.assertEqual(run.status, 'waiting')
        self.assertTrue(terminal['answer'])
        self.assertTrue(terminal['metadata']['teaching'])
        self.assertEqual(dispatched_payloads[0]['answer'], terminal['answer'])
        self.assertEqual(dispatched_payloads[0]['sources'], terminal['sources'])
        question = terminal['interaction']['interaction_id']
        reconnected = client.get(f'/api/pwanimate/agent-runs/{run.pk}/stream/')
        replay_body = asyncio.run(collect(reconnected))
        replay = json.loads(replay_body.split('event: response\ndata: ')[-1].strip())['data']
        self.assertEqual(replay['answer'], terminal['answer'])
        self.assertEqual(replay['message_id'], terminal['message_id'])
        self.assertEqual(replay['interaction']['interaction_id'], question)
        self.assertEqual(replay['sources'], terminal['sources'])
        self.assertEqual(replay['citations'], terminal['citations'])
        self.assertEqual(_terminal_payload(run)['answer'], terminal['answer'])
        msg = PwanimateMessage.objects.get(pk=terminal['message_id'])
        self.assertEqual(MessageSerializer(msg).data['content'], terminal['answer'])
        self.assertTrue(MessageSerializer(msg).data['teaching'])
        self.assertEqual(conv.messages.filter(role='assistant', task='teaching_pause').count(), 1)

    def test_generic_waiting_output_is_not_exposed(self):
        from pwanimate.api.agent_runs import _terminal_payload
        user, _, _, _, _, _, conv, _ = setup_mastery()
        source = PwanimateMessage.objects.create(conversation=conv, role='user', content='request')
        from pwanimate.orchestrator.types import OrchestrationRequest
        run = AgentRunService.pause(OrchestrationRequest(query='request',user=user,user_message_id=source.pk), {}, [])
        run.result_message = PwanimateMessage.objects.create(conversation=conv, role='assistant',content='private interim')
        run.save(update_fields=['result_message'])
        self.assertNotIn('answer', _terminal_payload(run))


class GreetingContinuityTests(TestCase):
    def setUp(self):
        self.case = ExecutionSourceTests()
        self.case.user, self.case.other, _, _, _, self.case.record, _, _ = setup_mastery()

    def test_greetings_skip_all_investigation_even_in_study_with_open_pdf(self):
        from pwanimate.orchestrator.service import PwanimateOrchestrator
        from pwanimate.orchestrator.types import OrchestrationRequest
        from pwanimate.ai.gateway.types import LLMResponse
        orchestrator = object.__new__(PwanimateOrchestrator)
        orchestrator.gateway = Mock()
        orchestrator.gateway.generate.return_value = LLMResponse(content='Hello!', provider='mock',model='mock',finish_reason='stop')
        orchestrator.tool_router = Mock()
        orchestrator.tool_selector = Mock()
        orchestrator._record_usage = Mock()
        with patch('pwanimate.orchestrator.document_context.DocumentTurnContext.resolve') as documents, \
             patch.object(TeachingService, 'applies') as teaching, \
             patch('pwanimate.orchestrator.agent.StudyAgent.run') as planner:
            for query in ('Wagwan Spark, how are you?','hi Spark, how are you?','hello'):
                for study, resources in ((False,[]),(True,[]),(True,[{'type':'document','documentId':'missing'}])):
                    before = orchestrator.gateway.generate.call_count
                    response = orchestrator.run(OrchestrationRequest(query=query,user=self.case.user,
                        study_mode=study, context_resources=resources))
                    self.assertEqual(response.answer, 'Hello!')
                    self.assertEqual(orchestrator.gateway.generate.call_count, before+1)
            documents.assert_not_called()
            teaching.assert_not_called()
            planner.assert_not_called()
        orchestrator.tool_router.route.assert_not_called()
        orchestrator.tool_selector.select.assert_not_called()

    def test_only_real_recent_authorized_activity_is_offered_readonly(self):
        from pwanimate.services.casual_context import greeting_context
        from django.test.utils import CaptureQueriesContext
        from django.db import connection
        self.assertEqual(greeting_context(self.case.user)[1], [])
        run = self.case.held_run()
        self.case.execute(run)
        session = resolve(self.case.user, run.user_message.conversation).session
        StudySessionService.pause_session(self.case.user, session.pk)
        with CaptureQueriesContext(connection) as queries:
            preferences, recent = greeting_context(self.case.user)
        self.assertEqual(recent[0]['recent_session']['status'], 'paused')
        self.assertEqual(recent[0]['recent_session']['recorded_activity'], 'topic_exposure')
        self.assertFalse(recent[0]['recent_session']['grants_write_authorization'])
        self.assertFalse(any(q['sql'].lstrip().upper().startswith(('INSERT','UPDATE','DELETE')) for q in queries))
        self.assertEqual(greeting_context(self.case.other)[1], [])
        Units.set_mode(self.case.user, 'disabled')
        self.assertEqual(greeting_context(self.case.user)[1], [])

    def test_multiple_sessions_do_not_produce_arbitrary_suggestion(self):
        from pwanimate.services.casual_context import greeting_context
        first = self.case.held_run()
        self.case.execute(first)
        second = self.case.held_run()
        self.case.execute(second)
        self.assertEqual(greeting_context(self.case.user)[1], [])


class HistoricalProjectionTests(TestCase):
    def setUp(self):
        self.case = ExecutionSourceTests()
        self.case.user, self.case.other, _, _, _, self.case.record, _, _ = setup_mastery()
        self.run = self.case.held_run()
        self.case.execute(self.run)

    def test_named_history_survives_new_ask_scope_and_stale_revision(self):
        from pwanimate.context.types import ContextPackage
        from pwanimate.models import SparkAcademicTrackingPreference
        conv = PwanimateConversation.objects.create(user=self.case.user)
        before = self.case.record.authorized_conversation_id
        context = Units.context_for_query(self.case.user, 'What have I studied in Linear Algebra?')
        self.assertTrue(context[0]['historical_read'])
        self.assertTrue(context[0]['teaching_progress'])
        self.assertFalse(Units.tracking_state(self.case.user, conv)['active_unit_tracking'])
        self.assertIn('topic_exposure', ContextPackage(query='history',academic_unit_context=context).format_context_text())
        SparkAcademicTrackingPreference.objects.filter(user=self.case.user).update(revision=99)
        retained = Units.context_for_query(self.case.user, 'What have I studied in Linear Algebra?')
        self.assertTrue(retained[0]['teaching_progress'])
        self.assertEqual(Units.context_for_query(self.case.other, 'What have I studied in Linear Algebra?'), [])
        Units.set_mode(self.case.user, 'disabled')
        self.assertEqual(Units.context_for_query(self.case.user, 'What have I studied in Linear Algebra?'), [])

    def test_inactive_enrollment_retains_readonly_named_history(self):
        record = SparkUnitActivity.objects.get(source_message_id=self.run.snapshot['execution_boundary_id']).record
        enrollment = record.enrollment
        enrollment.is_active = False
        enrollment.save(update_fields=['is_active'])
        context = Units.context_for_query(self.case.user, 'What have I studied in Linear Algebra?')
        self.assertTrue(context[0]['read_only'])
        self.assertTrue(context[0]['teaching_progress'])

    def test_large_context_is_valid_json_and_preserves_latest_identity_and_posture(self):
        import json
        from pwanimate.context.academic_history import bounded_academic_json
        data = [{'id':'record','unit_code':'MAT B201','semester_id':4,'historical_read':True,
            'write_authorization':'none from read', 'last_covered_concept':{'topic':'vector spaces','kind':'topic_exposure'},
            'activities':[{'excerpt':'x'*2000} for _ in range(100)]}]
        encoded = bounded_academic_json(data)
        self.assertLessEqual(len(encoded),18000)
        result = json.loads(encoded)[0]
        self.assertEqual(result['last_covered_concept']['topic'],'vector spaces')
        self.assertTrue(result['historical_read'])
        self.assertTrue(result['history_truncated'])

    def test_multiple_terms_require_scope_disambiguation(self):
        from documents.models import Semester, StudentUnitEnrollment
        from pwanimate.models import SparkUnitLearningRecord
        from django.utils import timezone
        record = self.case.record
        term = Semester.objects.create(number=2, academic_year=record.semester.academic_year,
            start_date=record.semester.start_date, end_date=record.semester.end_date)
        # Existing enrollment identity is unique per user/unit and can move terms.
        enrollment = record.enrollment
        enrollment.semester = term
        enrollment.save(update_fields=['semester'])
        SparkUnitLearningRecord.objects.create(user=self.case.user, enrollment=enrollment,
            academic_unit=record.academic_unit, semester=term, authorized_at=timezone.now())
        context = Units.context_for_query(self.case.user, 'What have I studied in Linear Algebra?')
        self.assertTrue(context[0]['clarification_required'])
        self.assertEqual(len(context[0]['units']),2)



class SnapshotCompatibilityTests(TestCase):
    def test_legacy_new_future_and_unknown_fields(self):
        from pwanimate.services.run_snapshots import decode_snapshot, SnapshotCompatibilityError
        legacy = {'query':'saved task','user_message_id':1}
        self.assertEqual(decode_snapshot(legacy)['snapshot_version'],1)
        self.assertEqual(decode_snapshot({**legacy,'snapshot_version':2})['snapshot_version'],2)
        for value in ({**legacy,'snapshot_version':3}, {**legacy,'snapshot_version':True},
                      {**legacy,'unexpected':'not executable'}, {**legacy,'execution_boundary_id':False}):
            with self.assertRaises(SnapshotCompatibilityError):
                decode_snapshot(value)
        with self.assertRaises(SnapshotCompatibilityError):
            decode_snapshot({**legacy,'snapshot_version':2},supported_version=1)
        with self.assertRaises(SnapshotCompatibilityError):
            decode_snapshot(legacy,require_execution_boundary=True)
        self.assertEqual(decode_snapshot({**legacy,'execution_boundary_id':2},
            require_execution_boundary=True)['execution_boundary_id'],2)

    def test_incompatible_snapshot_fails_before_creating_prospective_source(self):
        case = ExecutionSourceTests()
        case.user, case.other, _, _, _, case.record, _, _ = setup_mastery()
        run = case.held_run()
        run.snapshot['snapshot_version'] = 999
        before = PwanimateMessage.objects.count()
        with self.assertRaises(RunError):
            case.execute(run)
        self.assertEqual(PwanimateMessage.objects.count(),before)


class ProviderCompletionParityTests(TestCase):
    def test_all_native_json_and_stream_adapters_preserve_terminal_meaning(self):
        from pwanimate.tests.test_streaming import FakeStreamResponse, _stream_request, _sse_lines
        from pwanimate.ai.providers.groq import GroqLLMProvider
        from pwanimate.ai.providers.openrouter import OpenRouterLLMProvider
        from pwanimate.ai.providers.gemini import GeminiLLMProvider
        from pwanimate.ai.completion import normalize_finish_reason, is_complete
        for cls in (GroqLLMProvider, OpenRouterLLMProvider, GeminiLLMProvider):
            provider = cls(api_key='offline-test')
            for reason in ('stop','length','max_tokens','MAX_TOKENS','interrupted','content_filter','error',None):
                gemini = cls is GeminiLLMProvider
                data = ({'candidates':[{'content':{'parts':[{'text':'output'}]},'finishReason':reason}]}
                    if gemini else {'choices':[{'message':{'content':'output'},'finish_reason':reason}]})
                parsed = provider._parse_response(data, _stream_request(), 'offline')
                chunk = ({'candidates':[{'content':{'parts':[{'text':'output'}]},'finishReason':reason}]}
                    if gemini else {'choices':[{'delta':{'content':'output'},'finish_reason':reason}]})
                with patch.object(provider,'session') as session:
                    session.post.return_value = FakeStreamResponse(_sse_lines(chunk))
                    streamed = provider.generate_stream(_stream_request(), lambda text:None)
                self.assertEqual(parsed.finish_reason,normalize_finish_reason(reason))
                self.assertEqual(parsed.finish_reason,streamed.finish_reason)
                self.assertEqual(is_complete(parsed.finish_reason,parsed.content),reason=='stop')

    def test_cancel_during_generation_cannot_finalize_learning(self):
        case = ExecutionSourceTests()
        case.user, case.other, _, _, _, case.record, _, _ = setup_mastery()
        run = case.held_run()
        payload = case.execute(run, lambda:SparkAgentRun.objects.filter(pk=run.pk).update(cancel_requested=True))
        run.refresh_from_db()
        self.assertTrue(payload['cancelled'])
        self.assertEqual(run.status,'cancelled')
        self.assertFalse(SparkTeachingTurn.objects.filter(state__session__academic_conversation=run.user_message.conversation).exists())


class GreetingPreferenceTests(TestCase):
    def test_approved_preferences_are_read_without_greeting_word_overlap(self):
        from pwanimate.models import SparkMemoryPreference, SparkPersonalMemory
        from pwanimate.services.casual_context import greeting_context
        user, other, _, _, _, _, _, _ = setup_mastery()
        pref, _ = SparkMemoryPreference.objects.get_or_create(user=user)
        pref.enabled=True
        pref.consent_version=1
        pref.save()
        SparkPersonalMemory.objects.create(user=user,status='approved',memory_key='preference:length',
            text='Preferred response length: concise.')
        self.assertEqual(greeting_context(user)[0][0]['content'],'Preferred response length: concise.')
        self.assertEqual(greeting_context(other)[0],[])
        pref.enabled=False
        pref.save()
        self.assertEqual(greeting_context(user)[0],[])


class PartialFollowupTests(TestCase):
    def test_partial_durable_result_is_never_consumed_on_execute_or_replay(self):
        from pwanimate.models import SparkFollowUp
        from pwanimate.services.followups import execute_follow_up_action
        from django.utils import timezone
        user, _, _, _, _, _, conv, _ = setup_mastery()
        origin = PwanimateMessage.objects.create(conversation=conv,role='assistant',content='Explanation')
        offer = SparkFollowUp.objects.create(user=user,conversation=conv,message=origin,
            action_type='answer_followup',label='Example',payload={'question':'Give another example'},
            expires_at=timezone.now()+timezone.timedelta(hours=1))
        def partial(run):
            message = PwanimateMessage.objects.create(conversation=conv,role='assistant',content='partial =')
            SparkAgentRun.objects.filter(pk=run.pk).update(status='interrupted',result_message=message)
            return {'message_id':message.pk,'answer':message.content,'response_complete':False}
        with patch('pwanimate.services.agent_execution.execute_claimed',side_effect=partial) as execution:
            first = execute_follow_up_action(offer,user)
            again = execute_follow_up_action(offer,user)
        self.assertFalse(first['executed'])
        self.assertFalse(again['executed'])
        self.assertEqual(first['status'],'interrupted')
        self.assertEqual(execution.call_count,1)


class AttemptFailureFenceTests(TestCase):
    def test_late_failure_cannot_fail_a_newer_attempt(self):
        from pwanimate.services.agent_execution import mark_failed
        case = ExecutionSourceTests()
        case.user, case.other, _, _, _, case.record, _, _ = setup_mastery()
        run = case.held_run()
        run.attempt_id='older'
        run.save(update_fields=['attempt_id'])
        SparkAgentRun.objects.filter(pk=run.pk).update(attempt_id='newer')
        mark_failed(run,RuntimeError('old attempt'))
        run.refresh_from_db()
        self.assertEqual(run.status,'running')
        self.assertEqual(run.attempt_id,'newer')
