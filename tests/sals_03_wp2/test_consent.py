"""Study-tracking consent surfaced through chat/serialization contracts.

Covers the fix that attaches persisted Approve/Decline proposals to the
assistant reply that triggered detection instead of relying on generated
prose or a detached polling block. Runs on the disposable SQLite schema;
PostgreSQL concurrency stays with the WP0 runner.
"""
from django.test import TestCase
from pwanimate.api.serializers import ConversationDetailSerializer
from pwanimate.models import (
    PwanimateConversation,
    PwanimateMessage,
    PendingAction,
    SparkUnitActivity,
    SparkUnitLearningRecord,
)
from pwanimate.services.actions import ActionService
from pwanimate.services.unit_learning import UnitLearningService as Units
from tests.sals_02.test_foundation import setup_student


class ConsentSerializationTests(TestCase):
    def setUp(self):
        self.user, self.other, self.term, self.enrollment, self.second = setup_student()
        Units.preference(self.user)
        self.conversation = PwanimateConversation.objects.create(user=self.user)
        self.source = PwanimateMessage.objects.create(
            conversation=self.conversation, role='user', content='Explain matrices')
        self.reply = PwanimateMessage.objects.create(
            conversation=self.conversation, role='assistant', content='Matrices are arrays of numbers.')
        Units.process_message(self.source.pk)

    def payload(self):
        return ConversationDetailSerializer(self.conversation).data

    def assistant_actions(self):
        for msg in self.payload()['messages']:
            if msg['id'] == self.reply.id:
                return msg['academic_actions']
        self.fail('assistant message missing from serialization')

    def test_pending_proposal_attaches_to_assistant_reply(self):
        actions = self.assistant_actions()
        self.assertEqual(len(actions), 1)
        action = actions[0]
        self.assertEqual(action['action_type'], PendingAction.ACTION_TRACK_UNIT)
        self.assertEqual(action['status'], PendingAction.STATUS_PENDING)
        self.assertTrue(action['can_confirm'])
        self.assertEqual(action['params']['source_message_id'], self.source.pk)

    def test_user_message_carries_no_academic_actions(self):
        for msg in self.payload()['messages']:
            if msg['role'] == 'user':
                self.assertEqual(msg['academic_actions'], [])

    def test_duplicate_detection_never_duplicates_the_prompt(self):
        followup = PwanimateMessage.objects.create(
            conversation=self.conversation, role='user', content='Explain orthogonal lines')
        Units.process_message(followup.pk)
        Units.process_message(followup.pk)
        pending = PendingAction.objects.filter(
            conversation=self.conversation, status=PendingAction.STATUS_PENDING)
        self.assertEqual(pending.count(), 1)
        self.assertEqual(len(self.assistant_actions()), 1)

    def test_decline_clears_card_and_never_tracks(self):
        action = PendingAction.objects.get(conversation=self.conversation, status='pending')
        ActionService.cancel(self.user, action.pk)
        self.assertEqual(self.assistant_actions(), [])
        self.assertFalse(SparkUnitLearningRecord.objects.exists())
        self.assertFalse(SparkUnitActivity.objects.exists())
        # Tutoring continues: a later message in the same conversation does
        # not resurrect a prompt for the declined enrollment.
        later = PwanimateMessage.objects.create(
            conversation=self.conversation, role='user', content='Explain orthogonal lines')
        Units.process_message(later.pk)
        self.assertFalse(PendingAction.objects.filter(status='pending').exists())
        self.assertFalse(SparkUnitActivity.objects.exists())

    def test_approve_creates_unit_session_and_clears_card(self):
        action = PendingAction.objects.get(conversation=self.conversation, status='pending')
        ActionService.confirm(self.user, action.pk)
        self.assertEqual(self.assistant_actions(), [])
        record = SparkUnitLearningRecord.objects.get(user=self.user, enrollment=self.enrollment)
        self.assertEqual(record.sessions.count(), 1)
        # The triggering message is never imported as tracked activity.
        self.assertFalse(SparkUnitActivity.objects.filter(source_message=self.source).exists())
        # Subsequent authorized activity flows through the same record/session.
        later = PwanimateMessage.objects.create(
            conversation=self.conversation, role='user', content='Explain orthogonal lines')
        Units.process_message(later.pk)
        activity = SparkUnitActivity.objects.get(record=record)
        self.assertEqual(activity.source_message_id, later.pk)
        self.assertEqual(record.sessions.count(), 1)

    def test_expired_proposal_is_not_serialized(self):
        action = PendingAction.objects.get(conversation=self.conversation, status='pending')
        from django.utils import timezone
        from datetime import timedelta
        action.expires_at = timezone.now() - timedelta(seconds=1)
        action.save(update_fields=['expires_at'])
        self.assertEqual(self.assistant_actions(), [])

    def test_other_users_conversation_never_leaks_actions(self):
        other_conversation = PwanimateConversation.objects.create(user=self.other)
        other_reply = PwanimateMessage.objects.create(
            conversation=other_conversation, role='assistant', content='Hello')
        data = ConversationDetailSerializer(other_conversation).data
        for msg in data['messages']:
            self.assertEqual(msg['academic_actions'], [])
        self.assertNotEqual(str(other_reply.id), str(self.reply.id))


class TrackingStateTests(TestCase):
    """Server-verified tracking posture: the model may only describe what
    tracking_state() returns — never an imagined consent card or session."""

    def setUp(self):
        self.user, self.other, self.term, self.enrollment, self.second = setup_student()
        Units.preference(self.user)
        self.conversation = PwanimateConversation.objects.create(user=self.user)
        self.source = PwanimateMessage.objects.create(
            conversation=self.conversation, role='user', content='Explain matrices')
        Units.process_message(self.source.pk)

    def state(self, conversation=None):
        return Units.tracking_state(self.user, conversation=conversation or self.conversation)

    def test_pending_consent_is_reported_pending_not_active(self):
        state = self.state()
        self.assertEqual(state['preference_mode'], 'ask')
        self.assertFalse(state['tracking_disabled'])
        self.assertEqual(state['active_unit_tracking'], [])
        self.assertEqual(len(state['pending_consents']), 1)
        self.assertIn(self.enrollment.academic_unit.name, state['pending_consents'][0])
        self.assertEqual(state['declined_units'], [])

    def test_authorized_unit_session_reports_active_tracking(self):
        action = PendingAction.objects.get(conversation=self.conversation, status='pending')
        ActionService.confirm(self.user, action.pk)
        state = self.state()
        self.assertEqual(state['pending_consents'], [])
        self.assertEqual(len(state['active_unit_tracking']), 1)
        self.assertIn(self.enrollment.academic_unit.name, state['active_unit_tracking'][0])

    def test_declined_consent_is_reported_declined_not_pending(self):
        action = PendingAction.objects.get(conversation=self.conversation, status='pending')
        ActionService.cancel(self.user, action.pk)
        state = self.state()
        self.assertEqual(state['pending_consents'], [])
        self.assertEqual(state['active_unit_tracking'], [])
        self.assertEqual(len(state['declined_units']), 1)
        self.assertIn(self.enrollment.academic_unit.name, state['declined_units'][0])

    def test_disabled_tracking_reports_disabled_and_nothing_pending(self):
        Units.set_mode(self.user, 'disabled')
        state = self.state()
        self.assertTrue(state['tracking_disabled'])
        self.assertEqual(state['pending_consents'], [])
        self.assertEqual(state['active_unit_tracking'], [])

    def test_repeated_detection_never_duplicates_pending_consent(self):
        followup = PwanimateMessage.objects.create(
            conversation=self.conversation, role='user', content='Explain orthogonal lines')
        Units.process_message(followup.pk)
        Units.process_message(followup.pk)
        state = self.state()
        self.assertEqual(len(state['pending_consents']), 1)
        self.assertEqual(PendingAction.objects.filter(
            conversation=self.conversation, status='pending').count(), 1)

    def test_pending_card_in_other_conversation_is_not_promised(self):
        other_conversation = PwanimateConversation.objects.create(user=self.user)
        state = Units.tracking_state(self.user, conversation=other_conversation)
        self.assertEqual(state['pending_consents'], [])
        self.assertEqual(state['active_unit_tracking'], [])

    def test_prompt_block_carries_only_server_verified_state(self):
        from pwanimate.orchestrator.service import PwanimateOrchestrator
        from pwanimate.orchestrator.types import OrchestrationRequest
        request = OrchestrationRequest(query='Explain matrices', user=self.user,
            tracking_state=self.state())
        package, instruction = PwanimateOrchestrator()._apply_study_and_warning_context(
            request, 'Explain matrices', None, 'base instruction', [])
        self.assertIn('Academic tracking state', package.study_context_block)
        self.assertIn('pending_consents', package.study_context_block)
        self.assertIn(self.enrollment.academic_unit.name, package.study_context_block)
        self.assertIn('pending_consents is non-empty', instruction)
        self.assertIn('never re-ask', instruction)
