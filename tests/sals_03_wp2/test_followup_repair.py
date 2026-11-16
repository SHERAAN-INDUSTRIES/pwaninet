"""R1 lifecycle regressions; provider execution is isolated from network."""
from datetime import timedelta
from unittest.mock import patch
from django.test import TestCase
from django.contrib.auth import get_user_model
from django.utils import timezone
from pwanimate.models import SparkFollowUp, PwanimateConversation, PwanimateMessage
from pwanimate.services.followups import execute_follow_up_action, resolve_short_reply, FollowUpError

class RepairTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username='r1')
        self.conv = PwanimateConversation.objects.create(user=self.user)
        self.message = PwanimateMessage.objects.create(conversation=self.conv, role='assistant', content='Explanation')
        self.item = SparkFollowUp.objects.create(user=self.user, conversation=self.conv,
            message=self.message, action_type='answer_followup', label='Example',
            payload={'question': 'Give another example'}, expires_at=timezone.now()+timedelta(hours=1))

    def execute(self):
        return execute_follow_up_action(self.item, self.user)

    def test_expiry_persists_controlled_error(self):
        SparkFollowUp.objects.filter(pk=self.item.pk).update(expires_at=timezone.now()-timedelta(seconds=1))
        with self.assertRaises(FollowUpError) as caught:
            self.execute()
        self.assertEqual(caught.exception.code, 410)
        self.item.refresh_from_db()
        self.assertEqual(self.item.status, 'expired')

    def test_negative_replies_never_execute(self):
        for text in ['No', 'Not now', 'Skip', 'Maybe later']:
            SparkFollowUp.objects.filter(pk=self.item.pk).update(status='pending')
            item = resolve_short_reply(text, self.conv, self.user)
            self.assertEqual(item.status, 'cancelled')
            with self.assertRaises(FollowUpError):
                self.execute()

    def test_wrong_owner(self):
        other = get_user_model().objects.create_user(username='other')
        with self.assertRaises(FollowUpError):
            execute_follow_up_action(self.item, other)

    def test_wrong_conversation_and_unsupported_payload(self):
        for payload in [{'conversation_id': 'forged'}, {'arbitrary': 'value'}, {'question': []}]:
            SparkFollowUp.objects.filter(pk=self.item.pk).update(payload=payload)
            with self.assertRaises(FollowUpError):
                self.execute()

    def test_unsupported_action(self):
        SparkFollowUp.objects.filter(pk=self.item.pk).update(action_type='forged')
        with self.assertRaises(FollowUpError):
            self.execute()

    def test_topic_requires_real_owned_context(self):
        SparkFollowUp.objects.filter(pk=self.item.pk).update(action_type='teaching_topic', payload={})
        with self.assertRaises(FollowUpError):
            self.execute()

    def test_replay_and_duplicate_claim(self):
        def execute(run):
            # A second request during execution must not call provider again.
            duplicate = self.execute()
            self.assertEqual(duplicate['status'], 'executing')
            answer = PwanimateMessage.objects.create(conversation=self.conv, role='assistant', content='Actual example')
            run.result_message = answer
            run.status = 'completed'
            run.save()
            return {'message_id': answer.pk, 'answer': answer.content}
        with patch('pwanimate.services.agent_execution.execute_claimed', side_effect=execute) as worker:
            result = self.execute()
            self.assertTrue(result['executed'])
            self.assertEqual(self.execute(), result)
            self.assertEqual(worker.call_count, 1)
        self.assertEqual(self.conv.messages.filter(role='user').count(), 1)

    def test_failure_retry_same_user_turn(self):
        with patch('pwanimate.services.agent_execution.execute_claimed', side_effect=RuntimeError('provider')):
            with self.assertRaises(RuntimeError):
                self.execute()
        self.item.refresh_from_db()
        self.assertEqual(self.item.status, 'failed')
        with patch('pwanimate.services.agent_execution.execute_claimed', return_value={'message_id': self.message.pk}):
            self.assertTrue(self.execute()['executed'])
        self.assertEqual(self.conv.messages.filter(role='user').count(), 1)

    def test_interruption_fences_automatic_reexecution(self):
        SparkFollowUp.objects.filter(pk=self.item.pk).update(status='executing', claimed_at=timezone.now()-timedelta(hours=1))
        self.assertEqual(self.execute()['status'], 'interrupted')
        with self.assertRaises(FollowUpError):
            self.execute()

    def test_clarification_has_observable_execution(self):
        SparkFollowUp.objects.filter(pk=self.item.pk).update(action_type='clarification', payload={})
        with patch('pwanimate.services.agent_execution.execute_claimed', return_value={'message_id': self.message.pk}) as worker:
            self.assertTrue(self.execute()['executed'])
            self.assertEqual(worker.call_args.args[0].snapshot['query'], 'clarify')

    def test_empty_outcome_is_failure(self):
        with patch('pwanimate.services.agent_execution.execute_claimed', return_value={'action': 'placeholder'}):
            with self.assertRaises(RuntimeError):
                self.execute()
        self.item.refresh_from_db()
        self.assertEqual(self.item.status, 'failed')

class TeachingActionTests(TestCase):
    def setUp(self):
        from tests.sals_03_wp1.test_mastery import setup_mastery
        self.user, self.other, _, _, _, self.record, _, self.concept = setup_mastery()
        self.session = self.record.sessions.get()
        self.conv = self.session.conversation
        self.message = PwanimateMessage.objects.create(conversation=self.conv, role='assistant', content='Lesson')

    def item(self, action, **payload):
        return SparkFollowUp.objects.create(user=self.user, conversation=self.conv,
            message=self.message, action_type=action, label=action,
            payload={'session_id': str(self.session.pk), **payload}, expires_at=timezone.now()+timedelta(hours=1))

    def test_all_teaching_actions_use_durable_executor(self):
        from pwanimate.models import SparkAgentRun, SparkConceptEvidence
        for action in ['teaching_continue', 'practice_problem', 'teaching_topic']:
            item = self.item(action, concept_id=str(self.concept.pk))
            with patch('pwanimate.services.agent_execution.execute_claimed', return_value={'message_id': self.message.pk}) as worker:
                result = execute_follow_up_action(item, self.user)
                self.assertTrue(result['executed'])
                self.assertIn('teaching_state_id', worker.call_args.args[0].state)
            SparkAgentRun.objects.update(status='completed')
        self.assertFalse(SparkConceptEvidence.objects.exists())

    def test_revoked_consent_and_changed_enrollment(self):
        from pwanimate.models import SparkAcademicTrackingPreference
        item = self.item('teaching_continue')
        SparkAcademicTrackingPreference.objects.filter(user=self.user).update(mode='disabled')
        with self.assertRaises(FollowUpError):
            execute_follow_up_action(item, self.user)

    def test_cross_unit_concept_rejected(self):
        from pwanimate.services.concept_mastery import ConceptMasteryService
        item = self.item('teaching_topic', concept_id=str(self.concept.pk))
        with patch.object(ConceptMasteryService, 'owned_concept') as owned:
            owned.return_value.record_id = -1
            with self.assertRaises(FollowUpError):
                execute_follow_up_action(item, self.user)

    def test_actual_teaching_transition_and_receipt_replay(self):
        from pwanimate.services.teaching import TeachingService
        from pwanimate.orchestrator.types import OrchestrationRequest
        from pwanimate.models import SparkAgentRun, SparkTeachingTurn
        state = TeachingService.state_for(self.session, self.user)
        state.phase = 'EXPLAIN'
        state.save()
        source = PwanimateMessage.objects.create(conversation=self.conv, role='user', content='continue')
        run = SparkAgentRun.objects.create(user=self.user, user_message=source, status='running',
            continuation={'followup_action': 'teaching_continue'}, expires_at=timezone.now()+timedelta(hours=1))
        req = OrchestrationRequest(user=self.user, query='continue', user_message_id=source.pk)
        prepared = TeachingService.prepare(req, run=run)
        state.refresh_from_db()
        self.assertEqual(state.phase, 'DEMONSTRATE')
        # Existing teaching receipt is the idempotency boundary on retry.
        first = SparkTeachingTurn.objects.count()
        run.refresh_from_db()
        run.status = 'running'
        run.save()
        again = TeachingService.prepare(req, run=run)
        self.assertEqual(prepared['teaching_turn_id'], again['teaching_turn_id'])
        self.assertEqual(SparkTeachingTurn.objects.count(), first)

from django.test import TransactionTestCase
from django.db import connection, close_old_connections
from unittest import skipUnless
import threading

@skipUnless(connection.vendor == 'postgresql', 'Owner-run PostgreSQL locking test')
class FollowUpConcurrencyTests(TransactionTestCase):
    def test_simultaneous_clicks_claim_once(self):
        user = get_user_model().objects.create_user(username='r1-race')
        conv = PwanimateConversation.objects.create(user=user)
        message = PwanimateMessage.objects.create(conversation=conv, role='assistant', content='Origin')
        item = SparkFollowUp.objects.create(user=user, conversation=conv, message=message,
            action_type='answer_followup', label='Example', payload={'question':'Example please'},
            expires_at=timezone.now()+timedelta(hours=1))
        started, release = threading.Event(), threading.Event()
        results, errors = [], []
        def worker(run):
            started.set()
            if not release.wait(10):
                raise RuntimeError('test timeout')
            answer = PwanimateMessage.objects.create(conversation=conv, role='assistant', content='Result')
            return {'message_id':answer.pk}
        def click():
            close_old_connections()
            try:
                results.append(execute_follow_up_action(item, user))
            except Exception as exc:
                errors.append(exc)
            finally:
                close_old_connections()
        with patch('pwanimate.services.agent_execution.execute_claimed', side_effect=worker) as execute:
            thread = threading.Thread(target=click)
            thread.start()
            try:
                self.assertTrue(started.wait(10))
                self.assertEqual(execute_follow_up_action(item,user)['status'], 'executing')
                self.assertIsNone(resolve_short_reply('no',conv,user))
            finally:
                release.set()
                thread.join(10)
            self.assertFalse(errors)
            self.assertEqual(execute.call_count,1)
            self.assertTrue(results[0]['executed'])

class ApiRepairTests(RepairTests):
    def test_api_wrong_conversation_and_owner(self):
        from rest_framework.test import APIClient
        client = APIClient()
        client.force_authenticate(self.user)
        url = f'/api/pwanimate/follow-ups/{self.item.pk}/execute/'
        self.assertEqual(client.post(url, {'conversation_id': 'wrong'}, format='json').status_code,404)
        other = get_user_model().objects.create_user(username='api-other')
        client.force_authenticate(other)
        self.assertEqual(client.post(url, {}, format='json').status_code,404)

    def test_completed_origin_cannot_be_resurrected_as_interaction(self):
        from pwanimate.models import SparkAgentRun
        source = PwanimateMessage.objects.create(conversation=self.conv,role='user',content='Earlier')
        run = SparkAgentRun.objects.create(user=self.user,user_message=source,status='completed', expires_at=self.item.expires_at)
        SparkFollowUp.objects.filter(pk=self.item.pk).update(run=run,payload={'question':'Example','interaction_id':str(self.item.pk)})
        with self.assertRaises(FollowUpError):
            self.execute()
        run.refresh_from_db()
        self.assertEqual(run.status,'completed')
