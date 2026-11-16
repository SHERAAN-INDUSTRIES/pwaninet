"""Synthetic cross-chat recall and complete registered-tool reachability."""
import json
from unittest.mock import Mock, patch
from django.contrib.auth import get_user_model
from django.test import TestCase, SimpleTestCase
from django.utils import timezone
from pwanimate.models import (PwanimateConversation, PwanimateMessage, SparkPersonalMemory,
    SparkTeachingState, SparkTeachingTurn, PwanimateStudySession, PendingAction)
from pwanimate.services.personal_memory import PersonalMemoryService as Personal
from pwanimate.services.persistent_memory import PersistentMemoryService as Memory
from pwanimate.services.unit_learning import UnitLearningService as Units
from pwanimate.orchestrator.routing import select_strategy
from pwanimate.orchestrator.types import OrchestrationRequest
from pwanimate.orchestrator.agent import StudyAgent, EXCLUDED
from pwanimate.ai.gateway.types import LLMResponse
from pwanimate.tools.registry import get_default_tool_registry
from pwanimate.tools.domain.memory import RecallMemoryTool

QUERY = 'What were we devouring last time?'


class SavedRecallTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username='recent-memory-owner')
        self.other = get_user_model().objects.create_user(username='recent-memory-other')
        Personal.set_enabled(self.user, True)
        self.pref = Personal.set_automatic(self.user, True)
        self.old = PwanimateConversation.objects.create(user=self.user)
        self.new = PwanimateConversation.objects.create(user=self.user)
        source = PwanimateMessage.objects.create(conversation=self.old, role='user', content="I'm studying calculus.")
        answer = PwanimateMessage.objects.create(conversation=self.old, role='assistant', content='A recorded response.', task='agent')
        Memory.extract(answer.pk, self.pref.revision)
        self.source = source

    def test_generic_last_session_retrieves_real_cross_chat_learning(self):
        from pwanimate.services.conversation_memory import ConversationMemoryService
        result = ConversationMemoryService.search(self.user, QUERY, exclude_conversation_id=self.new.pk)
        self.assertTrue(result)
        self.assertIn('calculus', result[0]['content'])
        self.assertEqual(result[0]['message_id'], self.source.pk)
        self.assertEqual(result[0]['conversation_id'], str(self.old.pk))
        self.assertEqual(result[0]['match_type'], 'recent_learning_fact')

    def test_no_raw_transcripts_foreign_facts_or_response_preferences(self):
        SparkPersonalMemory.objects.create(user=self.user, text='Preferred response length: concise.', status='approved')
        PwanimateMessage.objects.create(conversation=self.old, role='user', content='A secret unsaved historical discussion.')
        SparkPersonalMemory.objects.create(user=self.other, text='A foreign memory.', category='learning', status='approved')
        result = Memory.retrieve(self.user, QUERY)
        self.assertNotIn('secret', str(result))
        self.assertNotIn('foreign', str(result))
        self.assertNotIn('Preferred response', str(result))
        self.assertEqual(Memory.retrieve(self.other, QUERY), [])

    def test_opt_out_and_revocation_take_effect_without_cache(self):
        self.assertTrue(Memory.retrieve(self.user, QUERY))
        SparkPersonalMemory.objects.filter(user=self.user).update(status='revoked')
        self.assertEqual(Memory.retrieve(self.user, QUERY), [])
        Personal.set_enabled(self.user, False)
        self.assertEqual(Memory.retrieve(self.user, QUERY), [])

    def test_named_unrelated_query_does_not_receive_recent_fallback(self):
        self.assertEqual(Memory.retrieve(self.user, 'What did I study about organic chemistry?'), [])

    def test_tool_reads_same_sources_without_resuming_or_consent_creation(self):
        sessions, actions = PwanimateStudySession.objects.count(), PendingAction.objects.count()
        result = RecallMemoryTool().execute(self.user, QUERY)
        self.assertTrue(result.success)
        self.assertTrue(result.data['facts'])
        self.assertTrue(result.data['read_only'])
        self.assertFalse(result.data['grants_write_authorization'])
        self.assertEqual(PwanimateStudySession.objects.count(), sessions)
        self.assertEqual(PendingAction.objects.count(), actions)
        self.assertFalse(RecallMemoryTool().execute(None, QUERY).success)


class AcademicRecentTests(TestCase):
    def setUp(self):
        from tests.sals_03_wp1.test_mastery import setup_mastery
        self.user, self.other, _, self.enrollment, self.second, self.record, _, _ = setup_mastery()
        self.session = self.record.sessions.get()
        self.source = self.session.conversation
        self.state = SparkTeachingState.objects.create(session=self.session, objective='Symmetric equations')
        response = PwanimateMessage.objects.create(conversation=self.source, role='assistant', content='Completed explanation.')
        self.turn = SparkTeachingTurn.objects.create(state=self.state, receipt_key='synthetic-completed',
            from_phase='DIAGNOSE', to_phase='EXPLAIN', action='ordinary_answer',
            payload={'teaching': {'objective': 'Symmetric equations'}},
            completed_at=timezone.now(), completed_message=response)

    def test_last_time_uses_completed_activity_across_conversations(self):
        new = PwanimateConversation.objects.create(user=self.user)
        before = PwanimateStudySession.objects.count()
        result = Units.context_for_query(self.user, QUERY)
        self.assertEqual(result[0]['last_covered_concept']['topic'], 'Symmetric equations')
        self.assertTrue(result[0]['historical_read'])
        self.assertEqual(result[0]['last_covered_concept']['message_id'], self.turn.completed_message_id)
        self.assertEqual(PwanimateStudySession.objects.count(), before)
        self.assertFalse(Units.tracking_state(self.user, new)['active_unit_tracking'])
        self.assertTrue(Units.detect(self.user, QUERY)['history_query'])

    def test_partial_turn_is_not_reported_as_completed(self):
        self.turn.completed_at = None
        self.turn.save(update_fields=['completed_at'])
        self.assertEqual(Units.context_for_query(self.user, QUERY), [])

    def test_disabled_tracking_blocks_history(self):
        Units.set_mode(self.user, 'disabled')
        self.assertEqual(Units.context_for_query(self.user, QUERY), [])
        self.assertEqual(Units.context_for_query(self.other, QUERY), [])

    def test_fresh_empty_record_does_not_replace_prior_completed_activity(self):
        from tests.sals_03_wp1.test_mastery import approve_record
        approve_record(self.user, self.second)
        result = Units.context_for_query(self.user, QUERY)
        self.assertEqual(result[0]['id'], str(self.record.pk))
        self.assertIn('History read grants no new writes', result[0]['write_authorization'])


class ToolReachabilityTests(SimpleTestCase):
    def test_last_session_colloquial_request_routes_to_recall(self):
        self.assertEqual(select_strategy(OrchestrationRequest(query=QUERY)).intent, 'recall')

    def test_every_registered_tool_is_in_planner_schema(self):
        registry = get_default_tool_registry()
        gateway = Mock()
        gateway.generate.return_value = LLMResponse(content='{"calls": []}', provider='mock', model='mock')
        StudyAgent(gateway, registry).run(OrchestrationRequest(query='Check my account information.'))
        prompt = gateway.generate.call_args.args[0].system_instruction
        self.assertFalse(EXCLUDED)
        for tool in registry.list_tools():
            self.assertIn('"name": "' + tool.name + '"', prompt)
        self.assertIn('recall_memory', prompt)

    def test_account_questions_do_not_bypass_planning(self):
        from tests.spark_r4.test_runtime import GroundingTests
        for query in ['What is my programme?', 'Explain my saved plan.', 'What are my unread chats?']:
            service = GroundingTests().service()
            with patch('pwanimate.services.social_updates.get_pending_friend_post_updates', return_value=[]), \
                 patch('pwanimate.orchestrator.agent.StudyAgent.run', return_value=None) as planner, \
                 patch.object(service, '_run_rag') as rag, patch.object(service, '_record_usage'):
                rag.return_value.metadata = {}
                service.run(OrchestrationRequest(query=query))
            planner.assert_called_once()

    def test_study_mode_does_not_swallow_explicit_platform_action(self):
        from tests.spark_r4.test_runtime import GroundingTests
        service = GroundingTests().service()
        with patch('pwanimate.services.social_updates.get_pending_friend_post_updates', return_value=[]), \
             patch('pwanimate.services.teaching.TeachingService.applies', return_value=True) as teaching, \
             patch('pwanimate.orchestrator.agent.StudyAgent.run', return_value=None) as planner, \
             patch.object(service, '_run_rag') as rag, patch.object(service, '_record_usage'):
            rag.return_value.metadata = {}
            service.run(OrchestrationRequest(query='Create a reminder to revise calculus.', study_mode=True,
                context_resources=[{'type': 'post', 'id': 'synthetic'}]))
        teaching.assert_not_called()
        planner.assert_called_once()

class NotificationReplayTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username='notify-replay-owner')
        self.other = get_user_model().objects.create_user(username='notify-replay-other')
        self.conv = PwanimateConversation.objects.create(user=self.user)
        self.source = PwanimateMessage.objects.create(conversation=self.conv, role='user',
            content='Notify me that the revision plan is ready.')

    def test_same_owned_request_and_inputs_create_exactly_one_notification(self):
        from notifications.models import NotificationObject
        registry = get_default_tool_registry()
        with patch('notifications.services.notification_service.invalidate_unread_count_cache'), \
             patch('channels.layers.get_channel_layer', return_value=None), \
             patch('notifications.queries.notification_queries.get_unread_count', return_value=1):
            first = registry.execute('send_notification', user=self.user,
                summary='Revision plan ready.', source_message_id=self.source.pk)
            second = registry.execute('send_notification', user=self.user,
                summary='Revision plan ready.', source_message_id=self.source.pk)
        self.assertTrue(first.success, first.error)
        self.assertTrue(second.success, second.error)
        self.assertEqual(first.data['notification_id'], second.data['notification_id'])
        self.assertTrue(second.data['replayed'])
        self.assertEqual(NotificationObject.objects.filter(recipient=self.user).count(), 1)

    def test_foreign_missing_source_and_future_request_cannot_write(self):
        from notifications.models import NotificationObject
        registry = get_default_tool_registry()
        for user, source in [(self.other, self.source.pk), (self.user, None)]:
            result = registry.execute('send_notification', user=user, summary='No write.', source_message_id=source)
            self.assertFalse(result.success)
        self.source.content = 'Notify me tomorrow to revise.'
        self.source.save(update_fields=['content'])
        self.assertFalse(registry.execute('send_notification', user=self.user,
            summary='No write.', source_message_id=self.source.pk).success)
        self.assertFalse(NotificationObject.objects.filter(recipient=self.user).exists())

class ActionVerbTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username='action-verb-owner')
        self.conv = PwanimateConversation.objects.create(user=self.user)

    def run_action(self, tool_name, query):
        from pwanimate.tools import ToolResult
        source = PwanimateMessage.objects.create(conversation=self.conv, role='user', content=query)
        tool, registry, gateway = Mock(), Mock(), Mock()
        tool.name = tool_name
        tool.parameters_schema = {'type': 'object', 'properties': {}}
        tool.to_dict.return_value = {'name': tool_name, 'parameters': tool.parameters_schema}
        tool.validate_parameters.return_value = {}
        tool.execute.return_value = ToolResult.ok({'executed': True})
        registry.list_tools.return_value = [tool]
        gateway.generate.side_effect = [
            LLMResponse(content=json.dumps({'calls': [{'tool': tool_name, 'arguments': {}}]}), provider='mock', model='mock'),
            LLMResponse(content='{"calls": []}', provider='mock', model='mock')]
        result = StudyAgent(gateway, registry).run(OrchestrationRequest(query=query,
            user=self.user, user_message_id=source.pk))
        return tool, result

    def test_all_mutation_verbs_reach_the_existing_tool_executor(self):
        cases = [
            ('create_reminder', 'Remind me to study at five.'),
            ('create_study_plan', 'Create a study plan.'),
            ('add_study_block', 'Add a study block.'),
            ('update_study_block', 'Reschedule my study block.'),
            ('remove_study_block', 'Cancel my study block.'),
            ('complete_study_block', 'Mark my study block done.'),
            ('send_message', 'Send a message to Alice.'),
            ('follow_user', 'Follow Alice.'),
            ('send_notification', 'Notify me that my plan is ready.'),
        ]
        for name, query in cases:
            with self.subTest(tool=name):
                tool, result = self.run_action(name, query)
                tool.execute.assert_called_once()
                self.assertTrue(result['result'].data['observations'][0]['result']['success'])

    def test_delete_request_cannot_authorize_creating_a_block(self):
        tool, _ = self.run_action('add_study_block', 'Delete my study block.')
        tool.execute.assert_not_called()

    def test_how_to_and_negation_do_not_authorize_mutations(self):
        for query in ['How do I create a study plan?', 'Do not create a study plan.']:
            tool, _ = self.run_action('create_study_plan', query)
            tool.execute.assert_not_called()

class RecallChatEndpointTests(TestCase):
    setUp = AcademicRecentTests.setUp
    def test_new_chat_receives_real_prior_topic_without_starting_tracking(self):
        from rest_framework.test import APIClient
        from pwanimate.api.views import PwanimateChatView
        from pwanimate.services.conversation import ConversationService
        from tests.spark_r4.test_runtime import GroundingTests
        client = APIClient()
        client.force_authenticate(self.user)
        conv = PwanimateConversation.objects.create(user=self.user)
        service = GroundingTests().service()
        sessions, pending = PwanimateStudySession.objects.count(), PendingAction.objects.count()
        with patch.object(PwanimateChatView, 'get_orchestrator', return_value=service), \
             patch.object(service, '_record_usage'), patch.object(service, '_request_metadata', return_value={}), \
             patch.object(ConversationService, 'schedule_embedding'), patch.object(ConversationService, 'schedule_digest'):
            response = client.post('/api/pwanimate/chat/', {'conversation_id': str(conv.pk), 'message': QUERY}, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        request = service.gateway.generate.call_args.args[0]
        self.assertIn('Symmetric equations', request.context.format_context_text())
        self.assertEqual(service.gateway.generate.call_count, 1)
        self.assertEqual(PwanimateStudySession.objects.count(), sessions)
        self.assertEqual(PendingAction.objects.count(), pending)

class HistoricalReadBoundaryTests(TestCase):
    setUp = AcademicRecentTests.setUp

    def test_retained_completed_history_does_not_grant_current_write_permission(self):
        from pwanimate.models import SparkAcademicTrackingPreference
        SparkAcademicTrackingPreference.objects.filter(user=self.user).update(revision=99)
        sessions, actions = PwanimateStudySession.objects.count(), PendingAction.objects.count()
        result = Units.context_for_query(self.user, QUERY)
        self.assertTrue(result[0]['read_only'])
        self.assertTrue(result[0]['historical_read'])
        self.assertIn('no new writes', result[0]['write_authorization'])
        self.assertEqual(PwanimateStudySession.objects.count(), sessions)
        self.assertEqual(PendingAction.objects.count(), actions)


from unittest import skipUnless
from django.db import connection
from django.test import TransactionTestCase


@skipUnless(connection.vendor == 'postgresql', 'Owner disposable PostgreSQL notification replay race')
class NotificationConcurrencyTests(TransactionTestCase):
    def test_two_attempts_for_one_source_publish_one_notification(self):
        import threading
        from django.db import close_old_connections
        from notifications.models import NotificationObject
        user = get_user_model().objects.create_user(username='notify-concurrent-owner')
        conv = PwanimateConversation.objects.create(user=user)
        source = PwanimateMessage.objects.create(conversation=conv, role='user', content='Notify me that revision is ready.')
        ready = threading.Barrier(2)
        outcomes = []
        def attempt():
            close_old_connections()
            try:
                ready.wait(timeout=10)
                outcomes.append(get_default_tool_registry().execute('send_notification', user=user,
                    summary='Revision ready.', source_message_id=source.pk))
            finally:
                close_old_connections()
        with patch('notifications.services.notification_service.invalidate_unread_count_cache'), \
             patch('channels.layers.get_channel_layer', return_value=None), \
             patch('notifications.queries.notification_queries.get_unread_count', return_value=1):
            workers = [threading.Thread(target=attempt) for _ in range(2)]
            for worker in workers: worker.start()
            for worker in workers: worker.join(15)
        self.assertFalse(any(worker.is_alive() for worker in workers))
        self.assertEqual(len(outcomes), 2)
        self.assertTrue(all(result.success for result in outcomes))
        self.assertEqual(len({result.data['notification_id'] for result in outcomes}), 1)
        self.assertEqual(NotificationObject.objects.filter(recipient=user).count(), 1)
