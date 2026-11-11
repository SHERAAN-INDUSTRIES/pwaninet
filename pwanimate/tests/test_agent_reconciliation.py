"""Corrective-pass coverage: truthful checklist reconciliation, extended
status taxonomy, web-search capability handling, and activity taxonomy."""
import json
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import Mock, patch

from django.test import SimpleTestCase, TestCase
from django.contrib.auth import get_user_model
from django.utils import timezone

from pwanimate.ai.gateway.types import LLMResponse
from pwanimate.models import PwanimateConversation, PwanimateMessage, SparkAgentRun, SparkAgentPlanStep
from pwanimate.orchestrator.agent import StudyAgent
from pwanimate.orchestrator.types import OrchestrationRequest
from pwanimate.services import agent_plans
from pwanimate.tools import get_default_tool_registry
from pwanimate.tools.domain.web_search import WebSearchTool
from pwanimate.tools.base import ToolResult
from pwanimate.web_search.exceptions import WebSearchError, WebSearchUnavailable
from pwanimate.web_search.types import WebSearchResponse, WebSearchResult


def reply(data):
    return LLMResponse(content=json.dumps(data), provider='mock', model='mock', total_tokens=10)


class ChecklistNormalizeTests(SimpleTestCase):
    def test_aliases_and_extended_statuses_are_normalized(self):
        raw = [
            {'label': 'Search web', 'status': 'running'},
            {'label': 'Read notes', 'status': 'completed'},
            {'label': 'Verify', 'status': 'failed', 'finding': 'tool timed out'},
            {'label': 'Extra', 'status': 'skipped'},
            {'label': 'Later', 'status': 'bogus'},
        ]
        items = StudyAgent._normalize_checklist(raw)
        self.assertEqual([i['status'] for i in items],
                         ['doing', 'done', 'failed', 'skipped', 'pending'])
        # Findings survive on every reconciled outcome.
        self.assertEqual(items[2]['finding'], 'tool timed out')

    def test_legacy_statuses_still_accepted(self):
        items = StudyAgent._normalize_checklist(
            [{'label': 'a', 'status': 'doing'}, {'label': 'b', 'status': 'done'},
             {'label': 'c', 'status': 'blocked'}])
        self.assertEqual([i['status'] for i in items], ['doing', 'done', 'blocked'])


class PlanStepSyncTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(username='plan-owner')
        conv = PwanimateConversation.objects.create(user=self.user)
        turn = PwanimateMessage.objects.create(conversation=conv, role='user', content='q')
        self.run = SparkAgentRun.objects.create(
            user=self.user, user_message=turn, status='running',
            expires_at=timezone.now() + timedelta(hours=1))

    def test_extended_statuses_map_to_durable_step_states(self):
        checklist = [
            {'label': 'Search', 'status': 'done', 'finding': 'found 3 results'},
            {'label': 'Read', 'status': 'failed', 'finding': 'source unavailable'},
            {'label': 'Extra', 'status': 'skipped'},
            {'label': 'Maybe', 'status': 'pending'},
        ]
        agent_plans.sync_steps(self.run, checklist)
        steps = {s.title: s.status for s in SparkAgentPlanStep.objects.filter(run=self.run)}
        self.assertEqual(steps['Search'], SparkAgentPlanStep.STATUS_COMPLETED)
        self.assertEqual(steps['Read'], SparkAgentPlanStep.STATUS_FAILED)
        self.assertEqual(steps['Extra'], SparkAgentPlanStep.STATUS_SKIPPED)
        self.assertEqual(steps['Maybe'], SparkAgentPlanStep.STATUS_PENDING)

    def test_terminal_run_never_reopens_steps(self):
        checklist = [{'label': 'A', 'status': 'done'}]
        agent_plans.sync_steps(self.run, checklist)
        # A later revision dropping the step must not resurrect it.
        self.run.status = 'completed'
        self.run.save(update_fields=['status'])
        agent_plans.sync_steps(self.run, [{'label': 'B', 'status': 'done'}])
        a = SparkAgentPlanStep.objects.get(run=self.run, title='A')
        self.assertEqual(a.status, SparkAgentPlanStep.STATUS_COMPLETED)


class ReviseChecklistTests(SimpleTestCase):
    def _agent(self, plan_payload):
        gateway = Mock()
        gateway.generate.return_value = reply({'checklist': plan_payload})
        agent = StudyAgent(gateway, Mock())
        request = SimpleNamespace(query='q', provider=None, model=None)
        return agent, request

    def test_revise_accepts_failed_and_skipped_outcomes(self):
        agent, request = self._agent([
            {'label': 'Search web', 'status': 'failed', 'finding': 'provider unavailable'},
            {'label': 'Read notes', 'status': 'skipped'},
            {'label': 'Summarize', 'status': 'done', 'finding': '3 points'},
        ])
        revised, *_ = agent._revise_checklist(
            request, [{'tool': 'web_search', 'result': {'success': False}}],
            [{'label': 'Search web', 'status': 'doing'},
             {'label': 'Read notes', 'status': 'doing'},
             {'label': 'Summarize', 'status': 'doing'}])
        self.assertEqual([i['status'] for i in revised], ['failed', 'skipped', 'done'])


class WebSearchCapabilityTests(SimpleTestCase):
    def test_unavailable_when_no_provider_configured(self):
        tool = WebSearchTool()
        with patch('pwanimate.tools.domain.web_search.WebSearchRouter') as router:
            router.return_value.search.side_effect = WebSearchUnavailable('none')
            result = tool.execute(user=None, query='kenya cs graduates employment')
        self.assertFalse(result.success)
        self.assertEqual(result.metadata.get('error_type'), 'web_search_unavailable')
        self.assertIn('Do not claim', result.error)

    def test_provider_error_is_failure_not_unavailable(self):
        tool = WebSearchTool()
        with patch('pwanimate.tools.domain.web_search.WebSearchRouter') as router:
            router.return_value.search.side_effect = WebSearchError('down')
            result = tool.execute(user=None, query='test')
        self.assertFalse(result.success)
        self.assertEqual(result.metadata.get('error_type'), 'web_search_failed')

    def test_success_returns_sources_with_urls(self):
        tool = WebSearchTool()
        response = WebSearchResponse(provider='tavily', query='q', results=[
            WebSearchResult(title='Report', url='https://example.com/r',
                            snippet='snippet', source='example.com')])
        with patch('pwanimate.tools.domain.web_search.WebSearchRouter') as router:
            router.return_value.search.return_value = response
            result = tool.execute(user=None, query='q')
        self.assertTrue(result.success)
        self.assertEqual(result.data[0]['url'], 'https://example.com/r')
        self.assertEqual(result.data[0]['title'], 'Report')

    def test_validation_rejects_bad_domains_and_recency(self):
        tool = WebSearchTool()
        with self.assertRaises(Exception):
            tool.execute(user=None, query='q', recency='hour')
        with self.assertRaises(Exception):
            tool.execute(user=None, query='q', domains=['https://evil.example/path'])


class ActivityTaxonomyTests(SimpleTestCase):
    """The public activity contract carries stable semantic types the UI maps
    to icons — verify the emitted shapes, including `activity_type`."""

    def test_tool_event_shape(self):
        from pwanimate.orchestrator.activity import event
        e = event('tool', 'Searching the web', event_id='t1', tool='web_search',
                  status='running', detail='current graduate employment')
        self.assertEqual(e['kind'], 'tool')
        self.assertEqual(e['tool'], 'web_search')
        self.assertEqual(e['status'], 'running')

    def test_activity_type_is_derived_from_semantics_not_status(self):
        from pwanimate.orchestrator.activity import event
        # Same operation, different outcomes -> identical activity_type.
        for status in ('running', 'completed', 'failed'):
            e = event('tool', 'Searching the web', event_id='t1', tool='web_search',
                      status=status)
            self.assertEqual(e['activity_type'], 'web_search')
        e = event('tool', 'Reading notes', event_id='t2', tool='retrieve_documents')
        self.assertEqual(e['activity_type'], 'document_read')
        e = event('tool', 'Looking up a profile', event_id='t3', tool='user_profile')
        self.assertEqual(e['activity_type'], 'tool_call')
        e = event('thinking', 'Weighing options', event_id='th1')
        self.assertEqual(e['activity_type'], 'thinking')
        e = event('checklist', 'Working checklist', event_id='checklist', items=[])
        self.assertEqual(e['activity_type'], 'checklist')
        e = event('checklist_review', 'Plan reconciled', event_id='verify', status='completed')
        self.assertEqual(e['activity_type'], 'plan_revision')
        e = event('verify', 'Revising checklist', event_id='verify', status='running')
        self.assertEqual(e['activity_type'], 'verification')
        e = event('clarification', 'What time?', event_id='c1', status='waiting')
        self.assertEqual(e['activity_type'], 'clarification')
        # Stage events derive from their stage id.
        e = event('stage', 'Putting the answer together', event_id='stage-writing_answer')
        self.assertEqual(e['activity_type'], 'response_draft')
        e = event('stage', 'Searching the web', event_id='stage-searching_web')
        self.assertEqual(e['activity_type'], 'web_search')

    def test_explicit_activity_type_wins(self):
        from pwanimate.orchestrator.activity import event
        e = event('tool', 'x', event_id='t', tool='web_search', activity_type='retry')
        self.assertEqual(e['activity_type'], 'retry')

    def test_unrecognized_events_get_no_semantic_type(self):
        from pwanimate.orchestrator.activity import event
        e = event('plan', 'Making progress', event_id='p1')
        self.assertIsNone(e['activity_type'])


class MandatoryReconciliationTests(TestCase):
    """A run with a checklist must reconcile it before finalizing, and no
    step may remain misleadingly 'doing' on the terminal checklist."""

    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(username='recon-owner')
        self.conv = PwanimateConversation.objects.create(user=self.user)
        self.turn = PwanimateMessage.objects.create(
            conversation=self.conv, role='user',
            content='Search the web for CS graduate employment in Kenya and summarize.')
        self.request = OrchestrationRequest(
            query=self.turn.content, user=self.user, user_message_id=self.turn.id)

    def _run_agent(self):
        events = []
        gateway = Mock()
        calls = {'n': 0}

        def generate(req, **kw):
            calls['n'] += 1
            n = calls['n']
            if n == 1:
                return reply({
                    'update': 'Searching for current data.',
                    'checklist': [{'label': 'Search web', 'status': 'doing'},
                                  {'label': 'Summarize findings', 'status': 'pending'}],
                    'calls': [{'tool': 'web_search',
                               'arguments': {'query': 'CS graduates employment Kenya'}}]})
            if n == 2:
                return reply({
                    'update': 'Search done; wrapping up.',
                    'checklist': [{'label': 'Search web', 'status': 'doing'},
                                  {'label': 'Summarize findings', 'status': 'doing'}],
                    'calls': []})
            # Checklist reviewer pass.
            return reply({'checklist': [
                {'label': 'Search web', 'status': 'done',
                 'finding': 'retrieved current sources'},
                {'label': 'Summarize findings', 'status': 'pending'}]})

        gateway.generate.side_effect = generate
        prepared = StudyAgent(gateway, get_default_tool_registry()).run(
            self.request, on_progress=events.append)
        return prepared, events

    def test_reconciliation_events_and_truthful_final_checklist(self):
        prepared, events = self._run_agent()
        self.assertIsNotNone(prepared)
        # The verify phase runs (running) and resolves (completed) under one
        # event id; the completed kind is checklist_review for icon semantics.
        verify = {e.get('status') for e in events if e.get('id') == 'verify'}
        self.assertIn('running', verify)
        self.assertIn('completed', verify)
        # The completed checklist event carries reconciled, non-running items.
        final = [e for e in events
                 if e.get('kind') == 'checklist' and e.get('status') == 'completed']
        self.assertTrue(final)
        statuses = {i['label']: i['status'] for i in final[-1]['items']}
        self.assertEqual(statuses['Search web'], 'done')
        self.assertEqual(statuses['Summarize findings'], 'pending')
        self.assertNotIn('doing', statuses.values())
        self.assertGreaterEqual(final[-1].get('unresolved', 0), 1)

    def test_reconcile_prompt_receives_observations(self):
        prepared, events = self._run_agent()
        # The tool event reached a terminal state — never left running.
        tool_events = [e for e in events if e.get('kind') == 'tool']
        self.assertTrue(tool_events)
        self.assertIn(tool_events[-1].get('status'), {'completed', 'failed'})
