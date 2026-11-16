"""Useful public explanations without forwarding private model narration."""
import json
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import Mock

from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase
from django.utils import timezone

from pwanimate.orchestrator.activity import event, public_event, public_events
from pwanimate.orchestrator.agent import StudyAgent
from pwanimate.orchestrator.types import OrchestrationRequest
from pwanimate.ai.gateway.types import LLMResponse
from pwanimate.tools.base import ToolResult

SECRET = 'PRIVATE PROMPT password=secret <script>alert(1)</script>'


class PublicExplanationTests(SimpleTestCase):
    def test_backend_matches_shared_browser_contract(self):
        from pathlib import Path
        path = Path(__file__).resolve().parents[2] / 'tests/spark/fixtures/activity-explanations.json'
        for fixture in json.loads(path.read_text()):
            self.assertEqual(public_event(fixture['input'])['summary'],fixture['summary'])

    def test_stage_explains_approach_and_answer_preparation(self):
        thinking = event('stage', SECRET, event_id='stage-thinking')
        writing = event('stage', SECRET, event_id='stage-writing_answer')
        self.assertIn('what your request needs', thinking['summary'])
        self.assertIn('available evidence', writing['summary'])
        self.assertNotIn(SECRET, str([thinking, writing]))

    def test_tool_explains_purpose_then_observed_outcome(self):
        start = event('tool', SECRET, tool='web_search', event_id='tool-1')
        end = event('tool', SECRET, tool='web_search', event_id='tool-1', status='completed', result_count=3)
        self.assertIn('external sources', start['summary'])
        self.assertIn('3 result(s)', end['summary'])
        self.assertNotIn('verified', end['summary'])
        self.assertEqual(start['id'], end['id'])
        self.assertEqual(start['activity_type'], end['activity_type'])
        self.assertEqual(public_event(end), end)

    def test_empty_failed_and_pending_actions_are_honest(self):
        empty = event('tool', SECRET, tool='retrieve_documents', status='completed', result_count=0)
        failed = event('tool', SECRET, tool='web_search', status='failed', result_count=7)
        proposed = event('tool', SECRET, tool='send_message', status='completed', awaiting_review=True)
        self.assertIn('no matching results', empty['summary'])
        self.assertIn('could not complete', failed['summary'])
        self.assertNotIn('result_count', failed)
        self.assertIn('has not been executed', proposed['summary'])

    def test_malicious_narration_is_replaced_not_forwarded(self):
        for kind in ['thinking', 'reasoning', 'plan', 'say', 'prompt']:
            self.assertIsNone(event(kind, SECRET))
        value = event('tool', SECRET, tool='web_search', detail=SECRET, summary=SECRET,
            reasoning=SECRET, arguments={'query':SECRET}, result_count=SECRET)
        self.assertNotIn(SECRET, str(value))
        self.assertIn('external sources', value['summary'])
        for count in [True, -1, 3.5, SECRET, {}]:
            value = event('tool', SECRET, tool='web_search', status='completed', result_count=count)
            self.assertNotIn('result_count', value)
        self.assertEqual(event('tool', '', tool='web_search', status='completed', result_count=999999)['result_count'],10000)

    def test_review_uses_actual_check_counts_not_model_findings(self):
        review = event('checklist_review', SECRET, status='completed', completed_checks=2, failed_checks=1)
        self.assertIn('2 successful', review['summary'])
        self.assertIn('1 failed', review['summary'])
        checklist = event('checklist', SECRET, items=[{'label':SECRET,'finding':SECRET,'status':'done'}, {'status':'blocked'}])
        self.assertIn('1 of 2', checklist['summary'])
        self.assertNotIn(SECRET, str(checklist))
        self.assertEqual(public_events([review,review]), [review])

    def test_agent_emits_only_grounded_result_counts(self):
        tool = Mock(name='public-web-tool')
        tool.name = 'web_search'
        tool.parameters_schema = {'type':'object','properties':{},'additionalProperties':False}
        tool.to_dict.return_value = {'name':'web_search','parameters':tool.parameters_schema}
        tool.validate_parameters.return_value = {}
        tool.execute.return_value = ToolResult.ok([
            {'title':'One','url':'https://example.com/1','snippet':'Source one'},
            {'title':'Two','url':'https://example.com/2','snippet':'Source two'}])
        registry = Mock()
        registry.list_tools.return_value = [tool]
        gateway = Mock()
        gateway.generate.side_effect = [
            LLMResponse(content=json.dumps({'update':SECRET,'calls':[{'tool':'web_search','arguments':{}}]}), provider='mock',model='mock'),
            LLMResponse(content=json.dumps({'calls':[]}),provider='mock',model='mock')]
        events = []
        request = OrchestrationRequest(query='Check sources',user=SimpleNamespace(pk=1, is_authenticated=False))
        prepared = StudyAgent(gateway,registry).run(request,on_progress=events.append)
        self.assertIsNotNone(prepared)
        completed = next(e for e in events if e['kind']=='tool' and e['status']=='completed')
        self.assertEqual(completed['result_count'],2)
        self.assertIn('2 result(s)',completed['summary'])
        self.assertNotIn(SECRET,str(events))


class ExplanationReplayTests(TestCase):
    def test_journal_and_message_history_preserve_public_summary(self):
        from pwanimate.models import PwanimateConversation, PwanimateMessage, SparkAgentRun
        from pwanimate.services.agent_journal import append, serialize_event
        from pwanimate.api.serializers import MessageSerializer
        user = get_user_model().objects.create_user(username='explanation-owner')
        conversation = PwanimateConversation.objects.create(user=user)
        source = PwanimateMessage.objects.create(conversation=conversation, role='user',content='Find sources')
        run = SparkAgentRun.objects.create(user=user,user_message=source,status='running',
            expires_at=timezone.now()+timedelta(minutes=15))
        value = event('tool','',tool='web_search',event_id='tool-1',status='completed',result_count=2)
        record = append(run,'activity',value)
        self.assertEqual(serialize_event(record)['payload'],value)
        self.assertEqual(MessageSerializer().get_activity(SimpleNamespace(activity=[value])),[value])
        self.assertIsNone(append(run,'activity',value))
