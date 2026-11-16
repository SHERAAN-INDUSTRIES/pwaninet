"""SALS 0.3 WP3 runtime pipeline tests: timing, fast path, observation reuse,
checklist-reconcile skip, and bounded answer continuation."""
import json
from types import SimpleNamespace as NS
from unittest.mock import Mock, patch

from django.test import SimpleTestCase, TestCase

from pwanimate.ai.gateway.types import ChatMessage, LLMRequest, LLMResponse
from pwanimate.orchestrator.agent import (
    StudyAgent,
    _checklist_needs_review,
    load_prior_observations,
    store_prior_observations,
)
from pwanimate.orchestrator.service import PwanimateOrchestrator
from pwanimate.orchestrator.timing import TimingRecorder
from pwanimate.orchestrator.types import OrchestrationRequest


class TimingRecorderTests(SimpleTestCase):
    def test_marks_counts_and_snapshot(self):
        timing = TimingRecorder()
        timing.mark('request_accepted')
        timing.mark('request_accepted')  # first mark wins
        timing.count('model_calls')
        timing.count('model_calls')
        timing.observe('llm_call_ms', 12.5)
        timing.observe('llm_call_ms', 7.5)
        snap = timing.snapshot()
        self.assertEqual(snap['counts'], {'model_calls': 2})
        self.assertEqual(snap['durations'], {'llm_call_ms': 20.0})
        self.assertIn('request_accepted', snap['marks'])
        self.assertNotIn('orchestrator_start', snap['marks'])
        self.assertGreaterEqual(snap['total_ms'], 0)


class RoutineTutoringClassifierTests(SimpleTestCase):
    def route(self, query):
        return PwanimateOrchestrator._routine_tutoring_route(query)

    def test_followup_cues_route_direct(self):
        for query in ('explain again', 'another example', 'simpler please',
                      'why does that work', 'continue', 'quiz me',
                      'give me a hint', 'what do you mean', 'recap that',
                      'show me your work'):
            self.assertEqual(self.route(query), 'direct', query)

    def test_math_requests_route_direct(self):
        for query in ('solve x^2 + 4x + 4 = 0', 'differentiate sin(x)',
                      'what is the derivative of x^3', 'factor 2x^2 + 8x + 8',
                      'prove that det(A) = det(A^T)'):
            self.assertEqual(self.route(query), 'direct', query)

    def test_academic_questions_route_rag(self):
        for query in ('explain photosynthesis',
                      'difference between mitosis and meiosis',
                      'how does the Krebs cycle work', 'teach me recursion',
                      'what are the causes of inflation',
                      'what is osmosis'):
            self.assertEqual(self.route(query), 'rag', query)

    def test_math_vocabulary_routes_direct(self):
        # Self-contained math needs no retrieval; unit context still flows
        # through the context package on the direct path.
        for query in ('what is a matrix', 'what is a determinant',
                      'explain eigenvalues'):
            self.assertEqual(self.route(query), 'direct', query)

    def test_tool_requiring_queries_never_fast_path(self):
        for query in ('search the web for photosynthesis',
                      'remind me tomorrow at 5pm',
                      'what is my schedule this week',
                      'find my notes on matrices',
                      'send a message to John',
                      'latest news about linear algebra',
                      'summarize my documents',
                      'when is my next study block',
                      'what grade did I get'):
            self.assertIsNone(self.route(query), query)

    def test_long_and_empty_queries_rejected(self):
        self.assertIsNone(self.route(''))
        self.assertIsNone(self.route('   '))
        self.assertIsNone(self.route('explain ' + 'x' * 300))


class AnswerContinuationTests(SimpleTestCase):
    def _service(self, responses):
        service = PwanimateOrchestrator.__new__(PwanimateOrchestrator)
        service.gateway = Mock()
        service.gateway.generate.side_effect = responses
        service.gateway.generate_stream.side_effect = responses
        return service

    def _request(self):
        return LLMRequest(
            task='tool',
            messages=[ChatMessage(role='user', content='explain again')],
            system_instruction='sys',
            max_tokens=100,
        )

    def test_length_finish_reason_continues_once(self):
        service = self._service([
            LLMResponse(content='partial ', provider='mock', model='m',
                        finish_reason='length', prompt_tokens=10,
                        completion_tokens=5, total_tokens=15, metadata={}),
            LLMResponse(content='answer', provider='mock', model='m',
                        finish_reason='stop', prompt_tokens=8,
                        completion_tokens=4, total_tokens=12, metadata={}),
        ])
        response = service._generate(self._request())
        self.assertEqual(service.gateway.generate.call_count, 2)
        self.assertEqual(response.content, 'partial answer')
        self.assertEqual(response.finish_reason, 'stop')
        self.assertTrue(response.metadata['continued'])
        self.assertEqual(response.metadata['recovered_from'], 'length')
        self.assertEqual(response.total_tokens, 27)

    def test_interrupted_stream_continues_and_preserves_partial(self):
        service = self._service([
            LLMResponse(content='half an ans', provider='mock', model='m',
                        finish_reason='interrupted', metadata={'interrupted': True}),
            LLMResponse(content='wer', provider='mock', model='m',
                        finish_reason='stop', metadata={}),
        ])
        response = service._generate(self._request())
        self.assertEqual(response.content, 'half an answer')
        self.assertTrue(response.metadata['continued'])

    def test_clean_finish_reason_single_call(self):
        service = self._service([
            LLMResponse(content='done', provider='mock', model='m',
                        finish_reason='stop', metadata={}),
        ])
        response = service._generate(self._request())
        self.assertEqual(service.gateway.generate.call_count, 1)
        self.assertNotIn('continued', response.metadata)

    def test_continuation_is_bounded_to_one(self):
        service = self._service([
            LLMResponse(content='a', provider='mock', model='m',
                        finish_reason='length', metadata={}),
            LLMResponse(content='b', provider='mock', model='m',
                        finish_reason='length', metadata={}),
        ])
        response = service._generate(self._request())
        self.assertEqual(service.gateway.generate.call_count, 2)
        self.assertEqual(response.content, 'ab')
        self.assertEqual(response.finish_reason, 'length')

    def test_continuation_failure_keeps_partial(self):
        service = self._service([
            LLMResponse(content='kept partial', provider='mock', model='m',
                        finish_reason='length', metadata={}),
        ])
        service.gateway.generate.side_effect = [
            LLMResponse(content='kept partial', provider='mock', model='m',
                        finish_reason='length', metadata={}),
            RuntimeError('provider down'),
        ]
        response = service._generate(self._request())
        self.assertEqual(response.content, 'kept partial')
        self.assertEqual(response.finish_reason, 'length')
        self.assertNotIn('continued', response.metadata)

    def test_streaming_marks_first_token_and_counts_calls(self):
        service = PwanimateOrchestrator.__new__(PwanimateOrchestrator)
        service.gateway = Mock()

        def stream_fn(req, on_token):
            on_token('streamed')
            return LLMResponse(content='streamed', provider='mock', model='m',
                               finish_reason='stop', metadata={})

        service.gateway.generate_stream.side_effect = stream_fn
        timing = TimingRecorder()
        chunks = []
        response = service._generate(self._request(), on_token=chunks.append, timing=timing)
        self.assertEqual(chunks, ['streamed'])
        self.assertEqual(timing.counts['model_calls'], 1)
        self.assertIn('first_token', timing.marks)
        self.assertIn('llm_call_ms', timing.durations)


class ChecklistReviewSkipTests(SimpleTestCase):
    def test_all_terminal_with_evidence_skips_review(self):
        checklist = [
            {'label': 'a', 'status': 'done', 'finding': 'found it'},
            {'label': 'b', 'status': 'failed', 'finding': 'tool errored'},
            {'label': 'c', 'status': 'skipped', 'finding': ''},
        ]
        self.assertFalse(_checklist_needs_review(checklist))

    def test_pending_or_doing_requires_review(self):
        self.assertTrue(_checklist_needs_review([{'label': 'a', 'status': 'pending'}]))
        self.assertTrue(_checklist_needs_review([{'label': 'a', 'status': 'doing'}]))
        self.assertTrue(_checklist_needs_review([{'label': 'a', 'status': 'blocked'}]))

    def test_unevidenced_done_still_requires_review(self):
        self.assertTrue(_checklist_needs_review([{'label': 'a', 'status': 'done', 'finding': ''}]))
        self.assertFalse(_checklist_needs_review([]))


class ObservationReuseTests(TestCase):
    def setUp(self):
        from django.contrib.auth import get_user_model
        from pwanimate.models import PwanimateConversation, PwanimateMessage

        self.User = get_user_model()
        self.user = self.User.objects.create_user(username='wp3obs', password='x')
        self.conv = PwanimateConversation.objects.create(user=self.user)
        self.msg = PwanimateMessage.objects.create(
            conversation=self.conv, role='user', content='check my timetable')
        self.scope = f"{self.user.pk}:{self.conv.pk}"

    def _entry(self, tool='local_time', success=True):
        return {'tool': tool, 'arguments': {},
                'result': {'success': success, 'data': {'now': '12:00'}, 'error': None}}

    def test_roundtrip_and_whitelist(self):
        store_prior_observations(self.scope, [
            self._entry('local_time'),
            self._entry('create_reminder'),   # write tool — never reused
            self._entry('send_message'),      # social proposal — never reused
            self._entry('web_search', success=False),  # failed — never reused
        ])
        loaded = load_prior_observations(self.scope)
        self.assertEqual([e['tool'] for e in loaded], ['local_time'])

    def test_scope_isolation(self):
        store_prior_observations(self.scope, [self._entry()])
        self.assertEqual(load_prior_observations('999:999'), [])
        self.assertEqual(load_prior_observations(None), [])
        self.assertEqual(load_prior_observations(''), [])

    def test_unversioned_observations_do_not_seed_new_runs(self):
        store_prior_observations(self.scope, [self._entry('local_time')])
        registry = Mock()
        registry.list_tools.return_value = []
        gateway = Mock()
        gateway.generate.return_value = LLMResponse(
            content=json.dumps({'calls': [], 'question': ''}),
            provider='mock', model='mock')
        request = OrchestrationRequest(
            query='what time is it', user=self.user, user_message_id=self.msg.pk)
        result = StudyAgent(gateway, registry).run(request)
        # R4: prior results lack current source/access versions. An empty
        # plan cannot turn cached content into current evidence.
        self.assertIsNone(result)
        self.assertEqual(gateway.generate.call_count, 1)

    def test_unscoped_request_never_seeds(self):
        store_prior_observations(self.scope, [self._entry('local_time')])
        registry = Mock()
        registry.list_tools.return_value = []
        gateway = Mock()
        gateway.generate.return_value = LLMResponse(
            content=json.dumps({'calls': []}), provider='mock', model='mock')
        # user_message_id that does not resolve to a conversation -> no reuse.
        request = OrchestrationRequest(
            query='what time is it', user=self.user, user_message_id=999999)
        result = StudyAgent(gateway, registry).run(request)
        self.assertIsNone(result)  # no evidence -> standard path bail, as before


class FastPathRoutingTests(TestCase):
    def setUp(self):
        from django.contrib.auth import get_user_model
        from pwanimate.ai.gateway.types import ChatMessage

        self.User = get_user_model()
        self.user = self.User.objects.create_user(username='wp3fast', password='x')
        self.ChatMessage = ChatMessage

    def _orchestrator(self, answer='Because the diagonal entries multiply.'):
        service = PwanimateOrchestrator.__new__(PwanimateOrchestrator)
        service.gateway = Mock()
        service.gateway.generate.return_value = LLMResponse(
            content=answer, provider='mock', model='mock',
            finish_reason='stop', metadata={})
        service.retrieval_service = Mock()
        service.context_engine = Mock()
        service.tool_router = Mock()
        service.tool_router.route.return_value = None
        service.tool_registry = Mock()
        service.tool_registry.list_tools.return_value = []
        service.tool_selector = Mock()
        service.tool_selector.required_web_query.return_value = None
        service.tool_selector.might_need_tool.return_value = False
        return service

    def test_followup_takes_direct_fast_path_with_single_model_call(self):
        service = self._orchestrator()
        request = OrchestrationRequest(
            query='explain again', user=self.user,
            history=[self.ChatMessage(role='assistant', content='Matrices map vectors.')])
        timing = TimingRecorder()
        request.timing = timing
        with patch('pwanimate.services.social_updates.get_pending_friend_post_updates', return_value=[]), \
             patch.object(PwanimateOrchestrator, '_record_usage'):
            response = service.run(request)
        self.assertEqual(response.metadata.get('fast_path'), 'routine_tutoring_direct')
        self.assertEqual(response.answer, 'Because the diagonal entries multiply.')
        # Exactly one generation call — zero planner rounds, zero tool calls.
        self.assertEqual(service.gateway.generate.call_count, 1)
        self.assertIn('fast_path_direct', timing.marks)

    def test_fast_path_emits_only_truthful_execution_stages(self):
        service = self._orchestrator()
        request = OrchestrationRequest(
            query='explain again', user=self.user,
            history=[self.ChatMessage(role='assistant', content='Matrices map vectors.')])
        events = []
        with patch('pwanimate.services.social_updates.get_pending_friend_post_updates', return_value=[]), \
             patch.object(PwanimateOrchestrator, '_record_usage'):
            service.run(request, on_progress=events.append)
        kinds = [e.get('kind') for e in events if isinstance(e, dict)]
        # No planner means no fabricated checklist/plan/tool-planning events;
        # only real execution stages (e.g. writing_answer) may appear.
        self.assertNotIn('checklist', kinds)
        self.assertNotIn('plan', kinds)
        self.assertIn('writing_answer', [e for e in events if isinstance(e, str)])

    def test_tool_requiring_query_still_reaches_planner_gate(self):
        service = self._orchestrator()
        request = OrchestrationRequest(
            query='remind me to revise matrices tomorrow', user=self.user)
        with patch('pwanimate.services.social_updates.get_pending_friend_post_updates', return_value=[]), \
             patch('pwanimate.orchestrator.agent.StudyAgent.run', return_value=None) as agent_run, \
             patch.object(PwanimateOrchestrator, '_record_usage'), \
             patch.object(PwanimateOrchestrator, '_run_rag') as rag:
            rag.return_value = NS(answer='ok', citations=[], sources=[], people=[],
                                  provider='mock', model='mock', prompt_tokens=1,
                                  completion_tokens=1, total_tokens=2, metadata={},
                                  quota_info=None, finish_reason='stop', warnings=[])
            service.run(request)
        agent_run.assert_called_once()
