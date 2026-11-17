"""Regression tests: submitting a clarification answer must continue the run.

Flow under test (what the student actually does):
  1. POST /api/pwanimate/chat/  -> Spark asks a clarifying question (round 0,
     no tool executed yet, run paused with status='waiting').
  2. POST /api/pwanimate/chat/ with resume_run_id + clarification_answer
     -> Spark is supposed to continue and produce a final answer.
"""
import json
from unittest.mock import Mock, patch

from django.contrib.auth import get_user_model
from rest_framework.test import APIClient
from django.test import TestCase

from pwanimate.ai.gateway.types import LLMResponse
from pwanimate.models import PwanimateConversation, SparkAgentInteraction, SparkAgentRun
from pwanimate.orchestrator.service import PwanimateOrchestrator


def reply(data):
    return LLMResponse(content=json.dumps(data), provider='mock', model='mock', total_tokens=10)


def answer(text):
    return LLMResponse(content=text, provider='mock', model='mock', total_tokens=10)


class ClarificationResumeTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(username='clar-owner')
        self.conversation = PwanimateConversation.objects.create(user=self.user)
        self.client = APIClient()
        self.client.force_authenticate(self.user)

    def _post(self, gateway, payload):
        from pwanimate.api.views import PwanimateChatView
        orchestrator = PwanimateOrchestrator(gateway=gateway)
        with patch.object(PwanimateChatView, 'orchestrator', orchestrator), \
             patch.object(orchestrator, '_record_usage'):
            return self.client.post('/api/pwanimate/chat/', payload, format='json')

    @staticmethod
    def _flaky_gateway(first_planner_reply):
        """Planner answers once, then always fails; answer generation works.

        Driven by inspecting the request instead of a fixed call list, so the
        test keeps its meaning even if the planner gains retries or the answer
        path makes a different number of calls.
        """
        calls = {'planner': 0, 'answer': 0}

        def generate(request):
            instruction = str(getattr(request, 'system_instruction', '') or '')
            if 'study assistant planner' in instruction:
                calls['planner'] += 1
                if calls['planner'] == 1:
                    return first_planner_reply
                raise RuntimeError('provider timeout')
            calls['answer'] += 1
            return answer('Reminder scheduled for tomorrow at 5 pm.')

        gateway = Mock()
        gateway.generate.side_effect = generate
        return gateway, calls

    def _pause_with_question(self, gateway):
        pause = self._post(gateway, {'message': 'Remind me to review trees'})
        self.assertEqual(pause.status_code, 200, getattr(pause, 'data', None))
        self.assertTrue(pause.data['metadata'].get('awaiting_clarification'),
                        pause.data.get('metadata'))
        run = SparkAgentRun.objects.get(pk=pause.data['metadata']['agent_run_id'])
        self.assertEqual(run.status, 'waiting')
        self.assertEqual(run.state.get('observed'), [])
        return run

    def test_resume_answers_when_planner_returns_no_further_calls(self):
        """Round 0 asks the question; after the answer the planner decides no
        more checks are needed and returns zero calls (the prompt tells it to).
        """
        gateway = Mock()
        gateway.generate.side_effect = [
            reply({'update': 'I need the time first.', 'question': 'When should I remind you?', 'calls': []}),
            reply({'update': 'I have everything I need.', 'calls': []}),
            answer('Reminder scheduled for tomorrow at 5 pm.'),
        ]
        run = self._pause_with_question(gateway)

        resume = self._post(gateway, {'resume_run_id': str(run.id),
                                      'clarification_answer': 'Tomorrow at 5 pm'})
        run.refresh_from_db()
        print('\n[no-further-calls] resume status:', resume.status_code,
              '| data:', getattr(resume, 'data', None), '| run:', run.status)
        self.assertEqual(resume.status_code, 200,
                         'Submitting the answer must continue the task, got %s: %s'
                         % (resume.status_code, getattr(resume, 'data', None)))
        self.assertNotEqual(run.status, 'failed')
        self.assertIn('Reminder scheduled', resume.data.get('answer', ''))

    def test_resume_answers_when_planner_executes_a_tool(self):
        gateway = Mock()
        gateway.generate.side_effect = [
            reply({'update': 'I need the time first.', 'question': 'When should I remind you?', 'calls': []}),
            reply({'update': 'Saving it now.',
                   'calls': [{'tool': 'create_reminder',
                              'arguments': {'title': 'Review trees',
                                            'due_at': '2030-01-01T17:00:00+00:00',
                                            'timezone': 'UTC'}}]}),
            reply({'calls': []}),
            answer('Reminder saved for 5 pm.'),
        ]
        run = self._pause_with_question(gateway)

        resume = self._post(gateway, {'resume_run_id': str(run.id),
                                      'clarification_answer': 'Tomorrow at 5 pm'})
        run.refresh_from_db()
        print('[tool-call] resume status:', resume.status_code, '| run:', run.status)
        self.assertEqual(resume.status_code, 200,
                         'Tool-backed resume failed: %s' % getattr(resume, 'data', None))
        self.assertEqual(run.status, 'completed')

    def test_resume_when_planner_call_fails(self):
        """Gateway hiccup on the resumed round must not kill a saved run."""
        gateway, calls = self._flaky_gateway(
            reply({'update': 'I need the time first.', 'question': 'When should I remind you?', 'calls': []}))
        run = self._pause_with_question(gateway)

        resume = self._post(gateway, {'resume_run_id': str(run.id),
                                      'clarification_answer': 'Tomorrow at 5 pm'})
        run.refresh_from_db()
        print('[planner-failure] resume status:', resume.status_code,
              '| data:', getattr(resume, 'data', None), '| run:', run.status,
              '| planner calls:', calls['planner'], '| answer calls:', calls['answer'])
        # The hiccup really happened on the resumed round (not just one call).
        self.assertGreaterEqual(calls['planner'], 2)
        self.assertEqual(resume.status_code, 200,
                         'A planner hiccup must fall back to a normal answer, got %s: %s'
                         % (resume.status_code, getattr(resume, 'data', None)))
        self.assertNotEqual(run.status, 'failed')
        self.assertGreaterEqual(calls['answer'], 1)

    def test_fresh_prompt_while_clarification_is_waiting_supersedes_it(self):
        """A genuinely new student turn must not be trapped behind the
        new-task guard while a clarification run is paused in WAITING.
        """
        gateway = Mock()
        gateway.generate.side_effect = [
            reply({'update': 'I need the time first.', 'question': 'When should I remind you?', 'calls': []}),
            reply({'update': 'That helps.', 'calls': []}),
            answer('Reminder scheduled for tomorrow at 5 pm.'),
        ]
        run = self._pause_with_question(gateway)
        self.assertEqual(run.status, SparkAgentRun.STATUS_WAITING)
        # The student moves on to a genuinely different question instead of
        # answering the parked one: the parked clarification must be abandoned
        # server-side, and this turn must be allowed to start a new task.
        fresh = self._post(gateway, {'message': 'What is photosynthesis?'})
        print('[supersede] fresh status:', fresh.status_code, getattr(fresh, 'data', None))
        run.refresh_from_db()
        print('[supersede] old run status now:', run.status, '| wait_reason:', run.wait_reason, '| result_msg:', run.result_message_id)
        self.assertEqual(fresh.status_code, 200, getattr(fresh, 'data', None))
        self.assertEqual(run.status, 'cancelled')
        self.assertFalse(
            SparkAgentInteraction.objects.filter(run=run, status='pending').exists())

    def test_ordinary_prompt_after_clarification_resume_completes_and_runs(self):
        """Full lifecycle: clarification -> answer -> resume -> completion, then
        an ordinary prompt must start a new, independent task instead of being
        rejected by the still-orphaned active-run guard."""
        gateway = Mock()
        gateway.generate.side_effect = [
            reply({'update': 'I need the time first.', 'question': 'When should I remind you?', 'calls': []}),
            reply({'calls': []}),
            final_answer,
        ]
        run = self._pause_with_question(gateway)

        resume = self._post(gateway, {'resume_run_id': str(run.id),
                                      'clarification_answer': 'Tomorrow at 5 pm'})
        self.assertEqual(resume.status_code, 200, getattr(resume, 'data', None))
        run.refresh_from_db()
        self.assertEqual(run.status, 'completed')

        ordinary = self._post(gateway, {'message': 'What is photosynthesis?'})
        self.assertEqual(ordinary.status_code, 200, getattr(ordinary, 'data', None))
        # The ordinary turn started a brand-new task instead of being rejected.
        self.assertEqual(
            SparkAgentRun.objects.filter(user=self.user, user_message__conversation=self.conversation).count(),
            2, 'clarification run + the new ordinary task run')
