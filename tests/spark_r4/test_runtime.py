import json
from unittest.mock import Mock, patch
from django.test import SimpleTestCase
from pwanimate.ai.exceptions import AIProviderConfigurationError, AIProviderRateLimitError
from pwanimate.ai.gateway.types import LLMResponse
from pwanimate.orchestrator.agent import StudyAgent
from pwanimate.orchestrator.routing import select_strategy
from pwanimate.orchestrator.types import OrchestrationRequest
from pwanimate.orchestrator.timing import TimingRecorder
from pwanimate.services.casual_context import is_greeting
from tests.spark_r4.benchmark import measure


class RoutingTests(SimpleTestCase):
    def test_intents_and_mixed_messages(self):
        cases = [
            ('Wagwan chief 😂', 'casual'), ('Good morning.', 'casual'),
            ('Thanks chief 😂', 'casual'),
            ('Good morning. Explain polymorphism.', 'explanation'),
            ('What were we discussing yesterday?', 'recall'),
            ('What did I study last time?', 'recall'),
            ('Do you remember my OOP session?', 'recall'),
            ('What topics have I completed?', 'recall'),
            ('Explain Newton\'s second law.', 'explanation'),
            ('Calculate the determinant of this matrix.', 'reasoning'),
            ('Debug this Java method.', 'reasoning'),
            ('Compare three previous examination papers.', 'complex'),
            ('Research recent changes in Java and compare them with my textbook.', 'complex'),
            ('Resume my previous OOP lesson.', 'continuation'),
            ('Give me another hint for the same question.', 'continuation'),
            ('continue', 'uncertain'), ('Why does that work?', 'uncertain'),
            ('What did I study last time and explain eigenvalues?', 'mixed'),
        ]
        for text, expected in cases:
            with self.subTest(text=text):
                self.assertEqual(select_strategy(OrchestrationRequest(query=text)).intent, expected)

    def test_documents_and_ambiguous_units_preserve_context_path(self):
        for query, expected in [('Wagwan chief, explain page 215 of my Java PDF.', 'document'),
                ('Summarize the highlighted paragraph.', 'document'),
                ('Explain OOP and calculus.', 'contextual'),
                ('Compare two documents.', 'complex')]:
            req = OrchestrationRequest(query=query, context_resources=[{'id': 'synthetic'}])
            self.assertEqual(select_strategy(req).intent, expected)
            self.assertFalse(select_strategy(req).direct)

    def test_greeting_does_not_swallow_operations(self):
        for text in ['hello; resume my lesson', 'thanks chief, search my documents',
                     'Wagwan 😂 explain recursion', 'hi explain page 2', 'hi; study OOP']:
            self.assertFalse(is_greeting(text))

    def test_mocked_call_budgets(self):
        results = measure(repeats=1)
        for label in ['greeting', 'emoji_greeting', 'thanks', 'explanation', 'mixed', 'recall', 'math', 'code']:
            self.assertEqual(results[label]['planning'], 0, label)
            self.assertEqual(results[label]['answers'], 1, label)
            self.assertEqual(results[label]['retrieval'], 0, label)
        self.assertEqual(results['complex']['planning'], 1)


class ExecutionTests(SimpleTestCase):
    def agent(self, error):
        gateway, registry = Mock(), Mock()
        gateway.generate.side_effect = error
        registry.list_tools.return_value = []
        return StudyAgent(gateway, registry), gateway

    def test_permanent_configuration_failure_does_not_fall_through(self):
        agent, gateway = self.agent(AIProviderConfigurationError('synthetic configuration failure'))
        with self.assertRaises(AIProviderConfigurationError):
            agent.run(OrchestrationRequest(query='Research Java changes.'))
        self.assertEqual(gateway.generate.call_count, 1)

    def test_all_candidates_in_cooldown_stop_immediate_recovery(self):
        agent, gateway = self.agent(AIProviderRateLimitError('cooldown', details={
            'attempted': [], 'skipped': [{'reason': 'cooldown'}]}))
        with self.assertRaises(AIProviderRateLimitError):
            agent.run(OrchestrationRequest(query='Research Java changes.'))
        self.assertEqual(gateway.generate.call_count, 1)

    def test_transient_gateway_failure_has_no_planner_retry(self):
        agent, gateway = self.agent(AIProviderRateLimitError('temporary overload'))
        self.assertIsNone(agent.run(OrchestrationRequest(query='Research Java changes.')))
        self.assertEqual(gateway.generate.call_count, 1)

    def test_numeric_diagnostics_never_copy_private_metadata(self):
        timing = TimingRecorder()
        timing.record_response(LLMResponse(content='private', provider='mock', model='mock',
            total_tokens=15, finish_reason='stop', metadata={
                'attempted_candidates': [{'secret': 'hidden'}], 'prompt': 'hidden'}))
        value = json.dumps(timing.snapshot())
        self.assertNotIn('private', value)
        self.assertNotIn('hidden', value)
        self.assertEqual(timing.counts['provider_attempts'], 1)

class GroundingTests(SimpleTestCase):
    def service(self):
        from tests.sals_wp3.test_runtime_pipeline import FastPathRoutingTests
        from pwanimate.context import ContextEngine
        service = FastPathRoutingTests()._orchestrator('Synthetic answer.')
        service.context_engine = ContextEngine()
        return service

    def test_recall_uses_supplied_records_and_does_not_enter_teaching(self):
        service = self.service()
        request = OrchestrationRequest(query='What did I study last time?', study_mode=True,
            academic_unit_context=[{'unit_code': 'OOP', 'historical_read': True,
                'write_authorization': 'none', 'last_covered_concept': {'topic': 'Interfaces'}}])
        with patch('pwanimate.services.teaching.TeachingService.applies') as teaching, \
             patch.object(service, '_record_usage'), patch.object(service, '_request_metadata', return_value={}):
            result = service.run(request)
        teaching.assert_not_called()
        service.retrieval_service.retrieve.assert_not_called()
        service.tool_router.route.assert_not_called()
        supplied = service.gateway.generate.call_args.args[0].context.academic_unit_context
        self.assertEqual(supplied, request.academic_unit_context)
        self.assertEqual(result.metadata['intent'], 'recall')
        self.assertTrue(request.study_mode)  # no mutation of canonical request

    def test_selected_document_retrieval_is_scoped_and_page_activity_truthful(self):
        from pwanimate.retrieval.types import RetrievalResponse, RetrievalResult, SourceType
        service = self.service()
        page = RetrievalResult(source=SourceType.DOCUMENT, object_id='17:215',
            title='Java', snippet='An interface defines a contract.', score=1,
            url='/documents/17/', citation='Java p215', metadata={'document_id': 17, 'page': 215, 'version': 2})
        service.retrieval_service.retrieve.return_value = RetrievalResponse(query='page', results=[])
        request = OrchestrationRequest(query='Explain page 215 of my Java PDF.', timing=TimingRecorder(),
            context_resources=[{'id': 17, 'type': 'document'}])
        events = []
        with patch('pwanimate.orchestrator.document_context.DocumentTurnContext.resolve', return_value=None), \
             patch('pwanimate.services.teaching.TeachingService.applies', return_value=False), \
             patch('pwanimate.services.social_updates.get_pending_friend_post_updates', return_value=[]), \
             patch.object(service, '_resolve_context_attachments', return_value=([], {})), \
             patch.object(service, '_resolve_context_resources', return_value=([17], [page], [], [])), \
             patch.object(service, '_request_metadata', return_value={}), patch.object(service, '_record_usage'):
            service.run(request, on_progress=events.append)
        service.retrieval_service.retrieve.assert_not_called()
        self.assertEqual(request.timing.counts['page_evidence_reused'], 1)
        self.assertIn('reading_document_page', events)
        context = service.gateway.generate.call_args.args[0].context
        self.assertIn('Java p215', str(context.citations))
        self.assertEqual(context.items[0].metadata['version'], 2)

    def test_same_tool_signature_runs_once_even_when_planner_repeats_it(self):
        from pwanimate.tools import ToolResult
        tool, registry, gateway = Mock(), Mock(), Mock()
        tool.name = 'local_time'
        tool.parameters_schema = {'type': 'object', 'properties': {}}
        tool.to_dict.return_value = {'name': 'local_time', 'parameters': tool.parameters_schema}
        tool.validate_parameters.return_value = {}
        tool.execute.return_value = ToolResult.ok({'time': 'synthetic'})
        registry.list_tools.return_value = [tool]
        plan = json.dumps({'calls': [{'tool': 'local_time', 'arguments': {}},
                                     {'tool': 'local_time', 'arguments': {}}]})
        gateway.generate.return_value = LLMResponse(content=plan, provider='mock', model='mock', finish_reason='stop')
        timing = TimingRecorder()
        StudyAgent(gateway, registry).run(OrchestrationRequest(query='Check the current time.', timing=timing))
        self.assertEqual(tool.execute.call_count, 1)
        self.assertEqual(timing.counts['tool_executions'], 1)
        self.assertGreaterEqual(timing.counts['tool_duplicates_skipped'], 1)
        self.assertLessEqual(gateway.generate.call_count, 2)

class RecoveryBudgetTests(SimpleTestCase):
    agent = ExecutionTests.agent

    def test_aggregate_exhaustion_does_not_attempt_answer_fallback(self):
        agent, gateway = self.agent(AIProviderRateLimitError('exhausted', provider='all',
            details={'attempted': ['mock:one'], 'skipped': []}))
        with self.assertRaises(AIProviderRateLimitError):
            agent.run(OrchestrationRequest(query='Research Java changes.'))
        self.assertEqual(gateway.generate.call_count, 1)

    def test_bad_plan_is_not_repeated_without_new_observations(self):
        agent, gateway = self.agent(None)
        gateway.generate.side_effect = None
        gateway.generate.return_value = LLMResponse(content='invalid planner output', provider='mock', model='mock')
        self.assertIsNone(agent.run(OrchestrationRequest(query='Research Java changes.')))
        self.assertEqual(gateway.generate.call_count, 1)

class DocumentFallbackTests(SimpleTestCase):
    def test_missing_page_evidence_retains_scoped_retrieval_and_explicit_sources(self):
        import time
        from pwanimate.retrieval.types import RetrievalResponse, SourceType
        for sources, expected in [(None, [SourceType.DOCUMENT]),
                ([SourceType.DOCUMENT, SourceType.POST], [SourceType.DOCUMENT, SourceType.POST])]:
            with self.subTest(sources=sources):
                service = GroundingTests().service()
                service.retrieval_service.retrieve.return_value = RetrievalResponse(query='page')
                request = OrchestrationRequest(query='Explain page 215 of my Java PDF.', sources=sources,
                    context_resources=[{'id': 17, 'type': 'document'}])
                with patch.object(service, '_resolve_context_attachments', return_value=([], {})), \
                     patch.object(service, '_resolve_context_resources', return_value=([17], [], [], [])), \
                     patch.object(service, '_request_metadata', return_value={}):
                    service._run_rag(request, request.query, time.perf_counter())
                lookup = service.retrieval_service.retrieve.call_args.args[0]
                self.assertEqual(lookup.sources, expected)
                self.assertEqual(lookup.filters, {'document_ids': [17]})

class ComplexDocumentTests(SimpleTestCase):
    def test_planner_document_lookup_intersects_selected_ids_and_unit(self):
        from types import SimpleNamespace
        from pwanimate.retrieval.types import RetrievalResponse
        gateway, registry = Mock(), Mock()
        registry.list_tools.return_value = []
        gateway.generate.side_effect = [
            LLMResponse(content=json.dumps({'calls': [{'tool': 'retrieve_documents',
                'arguments': {'query': 'exam comparison', 'academic_unit': 'OOP'}}]}), provider='mock', model='mock'),
            LLMResponse(content='{"calls": []}', provider='mock', model='mock')]
        request = OrchestrationRequest(query='Compare two documents.', document_context=SimpleNamespace(
            filter_document_ids=[17, 18], to_metadata=lambda: {'document_ids': [17, 18]}))
        with patch('pwanimate.retrieval.UnifiedRetrievalService') as retrieval:
            retrieval.return_value.retrieve.return_value = RetrievalResponse(query='comparison')
            StudyAgent(gateway, registry).run(request)
        lookup = retrieval.return_value.retrieve.call_args.args[0]
        self.assertEqual(lookup.filters, {'academic_unit': 'OOP', 'document_ids': [17, 18]})

class PlannerContextTests(SimpleTestCase):
    def test_academic_context_is_structural_and_not_duplicated_as_tool_evidence(self):
        gateway, registry = Mock(), Mock()
        registry.list_tools.return_value = []
        gateway.generate.return_value = LLMResponse(content='{"calls": []}', provider='mock', model='mock')
        request = OrchestrationRequest(query='Research a revision plan.', academic_unit_context=[{
            'unit_code': 'OOP', 'historical_read': True, 'write_authorization': 'none',
            'activities': [{'topic': 'x' * 20000}]}])
        self.assertIsNone(StudyAgent(gateway, registry).run(request))
        payload = json.loads(gateway.generate.call_args.args[0].messages[0].content)
        records = payload['personal_study']['unit_learning_records']
        self.assertEqual(records[0]['unit_code'], 'OOP')
        self.assertEqual(records[0]['write_authorization'], 'none')
        self.assertTrue(records[0]['history_truncated'])
        self.assertLess(len(json.dumps(records)), 18000)

class DirectAnswerBudgetTests(SimpleTestCase):
    def test_direct_explanation_keeps_academic_ceiling_and_explicit_override(self):
        from pwanimate.ai.gateway import get_task_policy
        for override in [None, 256]:
            service = GroundingTests().service()
            request = OrchestrationRequest(query='Derive the formula and explain every step.', max_tokens=override)
            with patch('pwanimate.services.teaching.TeachingService.applies', return_value=False), \
                 patch('pwanimate.services.social_updates.get_pending_friend_post_updates', return_value=[]), \
                 patch.object(service, '_record_usage'), patch.object(service, '_request_metadata', return_value={}):
                service.run(request)
            self.assertEqual(service.gateway.generate.call_args.args[0].max_tokens,
                override or get_task_policy('rag').max_output_tokens)
            self.assertEqual(service.gateway.generate.call_count, 1)
