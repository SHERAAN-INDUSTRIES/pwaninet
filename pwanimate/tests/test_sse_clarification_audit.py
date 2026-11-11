"""Regression coverage for the clarification/resume transport and the
durable continuation path (claim -> dispatch -> journal tail).

Covers the original defect (embedded widgets sent `Accept: text/event-stream`
only and DRF answered 406) plus the new guarantees: exactly-once resolution,
structured interactions, journal replay, cancellation, and retry.
"""
import asyncio
import json
from unittest.mock import Mock, patch

from asgiref.sync import async_to_sync
from django.contrib.auth import get_user_model
from django.test import TransactionTestCase, override_settings
from rest_framework.test import APIClient

from pwanimate.ai.gateway.types import LLMResponse
from pwanimate.models import (
    PwanimateConversation, PwanimateMessage, SparkAgentEvent,
    SparkAgentInteraction, SparkAgentRun,
)
from pwanimate.orchestrator.service import PwanimateOrchestrator
from pwanimate.api.views import PwanimateChatView
from pwanimate.services.conversation import ConversationService

EAGER = override_settings(CELERY_TASK_ALWAYS_EAGER=True)


def reply(data):
    return LLMResponse(content=json.dumps(data), provider='mock', model='mock', total_tokens=10)


def answer(text):
    return LLMResponse(content=text, provider='mock', model='mock', total_tokens=10)


async def _adrain(response):
    chunks = []
    async for chunk in response.streaming_content:
        chunks.append(chunk)
    return b''.join(chunks).decode('utf-8', errors='replace')


def drain(response):
    return async_to_sync(_adrain)(response)


def sse_events(body):
    """Parse `event:`/`data:` pairs from a drained SSE body."""
    events = []
    for block in body.split('\n\n'):
        block = block.strip()
        if not block or block.startswith(':'):
            continue
        kind, _, data = block.partition('\ndata: ')
        if kind.startswith('event: '):
            kind = kind[len('event: '):]
        try:
            events.append((kind, json.loads(data)))
        except (ValueError, TypeError):
            events.append((kind, data))
    return events


class AgentRunTestBase(TransactionTestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(username='run-owner')
        self.other = User.objects.create_user(username='run-other')
        self.conversation = PwanimateConversation.objects.create(user=self.user)
        self.client = APIClient()
        self.client.force_authenticate(self.user)
        self.other_client = APIClient()
        self.other_client.force_authenticate(self.other)
        # The mock orchestrator must stay patched for the WHOLE test: resumed
        # runs now execute through the durable path (claim -> dispatch ->
        # execute_claimed), which builds its own orchestrator instance.
        self.gateway = self._gateway()
        self.orchestrator = PwanimateOrchestrator(gateway=self.gateway)
        for patcher in (
            patch.object(PwanimateChatView, 'orchestrator', self.orchestrator),
            patch.object(self.orchestrator, '_record_usage'),
            # Eager Celery would otherwise run embedding/memory tasks inline
            # against real providers on every persisted message.
            patch.object(ConversationService, 'schedule_embedding'),
            patch.object(ConversationService, 'schedule_digest'),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    def _gateway(self, planner_replies=None):
        gateway = Mock()
        gateway.generate.side_effect = planner_replies or [
            reply({'update': 'I need the time first.', 'question': 'When should I remind you?', 'calls': []}),
            reply({'update': 'I have everything I need.', 'calls': []}),
            answer('Reminder scheduled for tomorrow at 5 pm.'),
        ]
        def _stream(req, tok):
            r = answer('Reminder scheduled for tomorrow at 5 pm.')
            tok(r.content)
            return r
        gateway.generate_stream.side_effect = _stream
        return gateway

    def _pause_run(self):
        """Send a message that pauses for clarification; return the run."""
        pause = self.client.post('/api/pwanimate/chat/',
                                 {'message': 'Remind me to review trees'},
                                 format='json',
                                 HTTP_ACCEPT='text/event-stream, application/json')
        self.assertEqual(pause.status_code, 200)
        drain(pause)
        return SparkAgentRun.objects.get(status='waiting')


@EAGER
class ClarificationResumeTransportTests(AgentRunTestBase):
    def test_sse_only_accept_negotiates_and_resumes(self):
        """The embedded widget's exact header must produce a real stream."""
        run = self._pause_run()
        resume = self.client.post('/api/pwanimate/chat/',
                                  {'resume_run_id': str(run.id),
                                   'clarification_answer': 'Tomorrow at 5 pm'},
                                  format='json',
                                  HTTP_ACCEPT='text/event-stream')
        self.assertEqual(resume.status_code, 200)
        self.assertIn('text/event-stream', resume['Content-Type'])
        events = sse_events(drain(resume))
        kinds = [k for k, _ in events]
        self.assertIn('response', kinds)
        final = [d for k, d in events if k == 'response'][-1]
        self.assertEqual(final['status_code'], 200)
        run.refresh_from_db()
        self.assertEqual(run.status, 'completed')

    def test_combined_accept_resumes(self):
        run = self._pause_run()
        resume = self.client.post('/api/pwanimate/chat/',
                                  {'resume_run_id': str(run.id),
                                   'clarification_answer': 'Tomorrow at 5 pm'},
                                  format='json',
                                  HTTP_ACCEPT='text/event-stream, application/json')
        self.assertEqual(resume.status_code, 200)
        events = sse_events(drain(resume))
        self.assertIn('response', [k for k, _ in events])

    def test_json_only_resume_still_works(self):
        run = self._pause_run()
        resume = self.client.post('/api/pwanimate/chat/',
                                  {'resume_run_id': str(run.id),
                                   'clarification_answer': 'Tomorrow at 5 pm'},
                                  format='json')
        self.assertEqual(resume.status_code, 200)
        self.assertIn('answer', resume.json())
        run.refresh_from_db()
        self.assertEqual(run.status, 'completed')

    def test_duplicate_submission_cannot_continue_twice(self):
        run = self._pause_run()
        payload = {'resume_run_id': str(run.id),
                   'clarification_answer': 'Tomorrow at 5 pm'}
        first = self.client.post('/api/pwanimate/chat/', payload, format='json')
        self.assertEqual(first.status_code, 200)
        second = self.client.post('/api/pwanimate/chat/', payload, format='json')
        self.assertIn(second.status_code, (400, 409, 503))
        self.assertEqual(PwanimateMessage.objects.filter(
            conversation__user=self.user, role='assistant').count(), 2)

    def test_expired_or_unknown_run_rejected(self):
        import uuid
        resume = self.client.post('/api/pwanimate/chat/',
                                  {'resume_run_id': str(uuid.uuid4()),
                                   'clarification_answer': 'hi'},
                                  format='json')
        self.assertIn(resume.status_code, (400, 404))

    def test_cross_user_run_rejected(self):
        run = self._pause_run()
        resume = self.other_client.post('/api/pwanimate/chat/',
                                        {'resume_run_id': str(run.id),
                                         'clarification_answer': 'Tomorrow at 5 pm'},
                                        format='json')
        self.assertIn(resume.status_code, (400, 404))
        run.refresh_from_db()
        self.assertEqual(run.status, 'waiting')


@EAGER
class InteractionAndJournalTests(AgentRunTestBase):
    def test_pause_creates_pending_interaction(self):
        run = self._pause_run()
        interaction = SparkAgentInteraction.objects.get(run=run)
        self.assertEqual(interaction.status, 'pending')
        self.assertEqual(interaction.prompt, 'When should I remind you?')
        self.assertEqual(interaction.input_type, 'text')

    def test_interaction_answer_persists_user_message(self):
        run = self._pause_run()
        interaction = SparkAgentInteraction.objects.get(run=run)
        resume = self.client.post('/api/pwanimate/chat/',
                                  {'interaction_id': str(interaction.id),
                                   'answer': 'Tomorrow at 5 pm'},
                                  format='json')
        self.assertEqual(resume.status_code, 200)
        interaction.refresh_from_db()
        self.assertEqual(interaction.status, 'answered')
        self.assertEqual(interaction.answer['text'], 'Tomorrow at 5 pm')
        self.assertTrue(PwanimateMessage.objects.filter(
            conversation__user=self.user, role='user',
            content='Tomorrow at 5 pm').exists())

    def test_interaction_double_resolve_rejected(self):
        run = self._pause_run()
        interaction = SparkAgentInteraction.objects.get(run=run)
        payload = {'interaction_id': str(interaction.id), 'answer': 'Tomorrow at 5 pm'}
        first = self.client.post('/api/pwanimate/chat/', payload, format='json')
        self.assertEqual(first.status_code, 200)
        second = self.client.post('/api/pwanimate/chat/', payload, format='json')
        self.assertIn(second.status_code, (400, 404, 409))

    def test_journal_records_ordered_events(self):
        run = self._pause_run()
        interaction = SparkAgentInteraction.objects.get(run=run)
        self.client.post('/api/pwanimate/chat/',
                         {'interaction_id': str(interaction.id),
                          'answer': 'Tomorrow at 5 pm'}, format='json')
        events = list(SparkAgentEvent.objects.filter(run=run).order_by('seq'))
        seqs = [e.seq for e in events]
        self.assertEqual(seqs, sorted(seqs))
        self.assertEqual(len(seqs), len(set(seqs)))
        self.assertTrue(any(e.type == 'run_resumed' for e in events))

    def test_events_endpoint_replay_and_cursor(self):
        run = self._pause_run()
        interaction = SparkAgentInteraction.objects.get(run=run)
        self.client.post('/api/pwanimate/chat/',
                         {'interaction_id': str(interaction.id),
                          'answer': 'Tomorrow at 5 pm'}, format='json')
        all_events = self.client.get(f'/api/pwanimate/agent-runs/{run.id}/events/')
        self.assertEqual(all_events.status_code, 200)
        payload = all_events.json()
        self.assertGreater(len(payload['events']), 0)
        after = payload['events'][0]['seq']
        replay = self.client.get(
            f'/api/pwanimate/agent-runs/{run.id}/events/?after={after}')
        self.assertEqual(replay.status_code, 200)
        replayed = replay.json()['events']
        self.assertTrue(all(e['seq'] > after for e in replayed))
        self.assertLess(len(replayed), len(payload['events']))

    def test_events_endpoint_owner_scoped(self):
        run = self._pause_run()
        resp = self.other_client.get(f'/api/pwanimate/agent-runs/{run.id}/events/')
        self.assertEqual(resp.status_code, 404)

    def test_run_detail_includes_plan_and_interaction(self):
        run = self._pause_run()
        resp = self.client.get(f'/api/pwanimate/agent-runs/{run.id}/')
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data['status'], 'waiting')
        self.assertIn('interaction', data)
        self.assertIn('plan', data)

    def test_cancel_waiting_run(self):
        run = self._pause_run()
        resp = self.client.post(f'/api/pwanimate/agent-runs/{run.id}/cancel/')
        self.assertEqual(resp.status_code, 200)
        run.refresh_from_db()
        self.assertEqual(run.status, 'cancelled')
        self.assertEqual(SparkAgentInteraction.objects.get(run=run).status, 'cancelled')

    def test_cancel_running_run_requests_stop(self):
        run = self._pause_run()
        SparkAgentRun.objects.filter(pk=run.pk).update(status='running')
        resp = self.client.post(f'/api/pwanimate/agent-runs/{run.id}/cancel/')
        self.assertEqual(resp.status_code, 200)
        run.refresh_from_db()
        self.assertTrue(run.cancel_requested)


GROUP_PLAN = {'update': 'I need a few details first.', 'questions': [
    {'question': 'Which programme are you in?', 'question_type': 'single',
     'question_options': [{'id': 'cs', 'label': 'Computer Science'},
                          {'id': 'it', 'label': 'Information Technology'}],
     'required': True},
    {'question': 'How many hours can you study each day?', 'question_type': 'text',
     'required': True},
    {'question': 'Which formats help you?', 'question_type': 'multi',
     'question_options': [{'id': 'video', 'label': 'Video'},
                          {'id': 'notes', 'label': 'Notes'}],
     'required': False},
], 'calls': []}

GROUP_ANSWERS = {'answers': {
    'q1': {'selected': ['cs']},
    'q2': {'text': 'Two hours daily'},
    'q3': {'selected': ['video'], 'text': 'short videos'},
}}


@EAGER
class GroupedInteractionTests(AgentRunTestBase):
    """One grouped interaction collects several answers and resolves once."""

    def _group_gateway(self):
        self.gateway.generate.side_effect = [
            reply(GROUP_PLAN),
            reply({'update': 'Got it, planning now.', 'calls': []}),
            answer('Here is your weekly study plan.'),
        ]

    def _pause_group(self):
        self._group_gateway()
        resp = self.client.post('/api/pwanimate/chat/', {'message': 'Plan my week'},
                                format='json',
                                HTTP_ACCEPT='text/event-stream, application/json')
        self.assertEqual(resp.status_code, 200)
        drain(resp)
        return SparkAgentRun.objects.get(status='waiting')

    def test_group_pause_creates_one_grouped_interaction(self):
        run = self._pause_group()
        interactions = list(SparkAgentInteraction.objects.filter(run=run))
        self.assertEqual(len(interactions), 1)
        interaction = interactions[0]
        self.assertEqual(interaction.input_type, 'group')
        self.assertEqual(interaction.kind, 'clarification')
        questions = interaction.serialize()['questions']
        self.assertEqual(len(questions), 3)
        self.assertEqual([q['id'] for q in questions], ['q1', 'q2', 'q3'])
        self.assertEqual(questions[0]['input_type'], 'single_choice')
        self.assertEqual(questions[1]['input_type'], 'text')
        self.assertEqual(questions[2]['input_type'], 'multi_choice')
        self.assertFalse(questions[2]['required'])

    def test_group_submission_resolves_once_and_resumes(self):
        run = self._pause_group()
        interaction = SparkAgentInteraction.objects.get(run=run)
        resume = self.client.post('/api/pwanimate/chat/',
                                  {'interaction_id': str(interaction.id),
                                   'answer': GROUP_ANSWERS}, format='json')
        self.assertEqual(resume.status_code, 200)
        interaction.refresh_from_db()
        self.assertEqual(interaction.status, 'answered')
        self.assertEqual(set(interaction.answer['answers']), {'q1', 'q2', 'q3'})
        # Readable summary persisted for history/prompts.
        self.assertIn('Computer Science', interaction.answer['text'])
        self.assertIn('Two hours daily', interaction.answer['text'])
        run.refresh_from_db()
        self.assertEqual(run.status, 'completed')
        # Exactly one persisted user message carries the group answer.
        answers = PwanimateMessage.objects.filter(
            conversation__user=self.user, role='user',
            content__contains='Computer Science')
        self.assertEqual(answers.count(), 1)

    def test_group_missing_required_answer_rejected(self):
        run = self._pause_group()
        interaction = SparkAgentInteraction.objects.get(run=run)
        resume = self.client.post('/api/pwanimate/chat/',
                                  {'interaction_id': str(interaction.id),
                                   'answer': {'answers': {'q2': {'text': 'Two hours'}}}},
                                  format='json')
        self.assertEqual(resume.status_code, 400)
        interaction.refresh_from_db()
        self.assertEqual(interaction.status, 'pending')
        run.refresh_from_db()
        self.assertEqual(run.status, 'waiting')

    def test_group_invalid_option_rejected(self):
        run = self._pause_group()
        interaction = SparkAgentInteraction.objects.get(run=run)
        bad = {'answers': {'q1': {'selected': ['astronomy']},
                           'q2': {'text': 'Two hours'}}}
        resume = self.client.post('/api/pwanimate/chat/',
                                  {'interaction_id': str(interaction.id),
                                   'answer': bad}, format='json')
        self.assertEqual(resume.status_code, 400)
        interaction.refresh_from_db()
        self.assertEqual(interaction.status, 'pending')

    def test_group_unknown_question_id_rejected(self):
        run = self._pause_group()
        interaction = SparkAgentInteraction.objects.get(run=run)
        bad = {'answers': {'q9': {'text': 'sneaky'}}}
        resume = self.client.post('/api/pwanimate/chat/',
                                  {'interaction_id': str(interaction.id),
                                   'answer': bad}, format='json')
        self.assertEqual(resume.status_code, 400)

    def test_group_optional_question_may_be_skipped(self):
        run = self._pause_group()
        interaction = SparkAgentInteraction.objects.get(run=run)
        partial = {'answers': {'q1': {'selected': ['it']},
                               'q2': {'text': 'One hour'}}}
        resume = self.client.post('/api/pwanimate/chat/',
                                  {'interaction_id': str(interaction.id),
                                   'answer': partial}, format='json')
        self.assertEqual(resume.status_code, 200)
        interaction.refresh_from_db()
        self.assertNotIn('q3', interaction.answer['answers'])

    def test_group_double_submission_rejected(self):
        run = self._pause_group()
        interaction = SparkAgentInteraction.objects.get(run=run)
        payload = {'interaction_id': str(interaction.id), 'answer': GROUP_ANSWERS}
        first = self.client.post('/api/pwanimate/chat/', payload, format='json')
        self.assertEqual(first.status_code, 200)
        second = self.client.post('/api/pwanimate/chat/', payload, format='json')
        self.assertIn(second.status_code, (400, 404, 409))
        self.assertEqual(PwanimateMessage.objects.filter(
            conversation__user=self.user, role='user',
            content__contains='Computer Science').count(), 1)
