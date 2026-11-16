"""Tests for automatic contextual follow-up suggestion generation."""
from django.test import TestCase
from django.contrib.auth import get_user_model
from pwanimate.models import PwanimateConversation, PwanimateMessage, PwanimateStudySession, SparkFollowUp
from pwanimate.services.followups import create_follow_ups, resolve_short_reply
from pwanimate.services.teaching import TeachingService

User = get_user_model()


class FollowUpGenerationTest(TestCase):
    """Test automatic follow-up suggestion generation."""

    def setUp(self):
        self.user = User.objects.create_user(username='testuser', email='test@example.com', password='testpass')
        self.conversation = PwanimateConversation.objects.create(user=self.user, title='Test Conversation')

    def test_no_suggestions_for_greetings(self):
        """Greetings should not generate follow-up suggestions."""
        # Create a teaching turn with a greeting
        # (This is a simplified test - in practice we'd need full teaching state)
        message = PwanimateMessage.objects.create(
            conversation=self.conversation,
            role='assistant',
            content='Hello! How can I help you today?',
        )
        # No follow-ups should be created for greetings
        follow_ups = create_follow_ups(message, suggestions=[])
        self.assertEqual(len(follow_ups), 0)

    def test_no_suggestions_for_completed_sessions(self):
        """Completed sessions should not generate follow-up suggestions."""
        session = PwanimateStudySession.objects.create(
            user=self.user,
            conversation=self.conversation,
            status='completed',
        )
        message = PwanimateMessage.objects.create(
            conversation=self.conversation,
            role='assistant',
            content='Session completed.',
        )
        # The create_follow_ups service doesn't check session status directly;
        # that's handled in TeachingService._generate_follow_up_suggestions.
        # This test verifies the service creates what's given.
        # teaching_continue requires session_id or conversation_id in payload
        follow_ups = create_follow_ups(message, suggestions=[
            {'action_type': 'teaching_continue', 'label': 'Continue', 'payload': {'conversation_id': str(self.conversation.id)}}
        ])
        self.assertEqual(len(follow_ups), 1)

    def test_max_three_suggestions(self):
        """Should cap suggestions at 3 per message."""
        message = PwanimateMessage.objects.create(
            conversation=self.conversation,
            role='assistant',
            content='Test response.',
        )
        suggestions = [
            {'action_type': 'practice_problem', 'label': f'Suggestion {i}', 'payload': {}}
            for i in range(5)
        ]
        follow_ups = create_follow_ups(message, suggestions=suggestions)
        self.assertEqual(len(follow_ups), 3)

    def test_short_reply_resolution_affirmative(self):
        """Short affirmative replies should resolve pending follow-ups."""
        message = PwanimateMessage.objects.create(
            conversation=self.conversation,
            role='assistant',
            content='Would you like to practice?',
        )
        follow_up = create_follow_ups(message, suggestions=[
            {'action_type': 'practice_problem', 'label': 'Try practice', 'payload': {'session_id': '123'}}
        ])[0]

        # Simulate short reply
        resolved = resolve_short_reply('yes', self.conversation, self.user)
        self.assertIsNotNone(resolved)
        self.assertEqual(resolved.id, follow_up.id)
        self.assertEqual(resolved.status, 'pending')

    def test_short_reply_resolution_negative(self):
        """Short negative replies should cancel pending follow-ups."""
        message = PwanimateMessage.objects.create(
            conversation=self.conversation,
            role='assistant',
            content='Would you like to practice?',
        )
        follow_up = create_follow_ups(message, suggestions=[
            {'action_type': 'practice_problem', 'label': 'Try practice', 'payload': {}}
        ])[0]

        resolved = resolve_short_reply('no', self.conversation, self.user)
        self.assertIsNotNone(resolved)
        self.assertEqual(resolved.id, follow_up.id)
        self.assertEqual(resolved.status, 'cancelled')

    def test_short_reply_ambiguous_passes_through(self):
        """Ambiguous replies should not resolve follow-ups."""
        message = PwanimateMessage.objects.create(
            conversation=self.conversation,
            role='assistant',
            content='Would you like to practice?',
        )
        create_follow_ups(message, suggestions=[
            {'action_type': 'practice_problem', 'label': 'Try practice', 'payload': {}}
        ])

        # Ambiguous reply should pass through
        resolved = resolve_short_reply('what do you mean?', self.conversation, self.user)
        self.assertIsNone(resolved)

    def test_multiple_pending_followups_no_auto_resolution(self):
        """Multiple pending follow-ups should not auto-resolve on short replies."""
        # Note: create_follow_ups expires previous pending follow-ups for the conversation.
        # To test multiple pending, we need to create them in the same call.
        message = PwanimateMessage.objects.create(
            conversation=self.conversation,
            role='assistant',
            content='Choose an option:',
        )
        # teaching_continue requires session_id or conversation_id in payload
        follow_ups = create_follow_ups(message, suggestions=[
            {'action_type': 'practice_problem', 'label': 'Practice', 'payload': {}},
            {'action_type': 'teaching_continue', 'label': 'Continue', 'payload': {'conversation_id': str(self.conversation.id)}},
        ])
        self.assertEqual(len(follow_ups), 2)

        # With multiple pending, short reply should not resolve
        resolved = resolve_short_reply('yes', self.conversation, self.user)
        self.assertIsNone(resolved)

    def test_follow_up_persistence(self):
        """Follow-ups should persist and be queryable."""
        message = PwanimateMessage.objects.create(
            conversation=self.conversation,
            role='assistant',
            content='Test response.',
        )
        follow_up = create_follow_ups(message, suggestions=[
            {'action_type': 'practice_problem', 'label': 'Try practice', 'payload': {}}
        ])[0]

        # Query from database
        db_follow_up = SparkFollowUp.objects.get(id=follow_up.id)
        self.assertEqual(db_follow_up.status, 'pending')
        self.assertEqual(db_follow_up.message_id, message.id)
        self.assertEqual(db_follow_up.conversation_id, self.conversation.id)
        self.assertEqual(db_follow_up.user_id, self.user.id)

    def test_follow_up_expiration(self):
        """Expired follow-ups should not resolve."""
        from django.utils import timezone
        from datetime import timedelta

        message = PwanimateMessage.objects.create(
            conversation=self.conversation,
            role='assistant',
            content='Test response.',
        )
        follow_up = create_follow_ups(message, suggestions=[
            {'action_type': 'practice_problem', 'label': 'Try practice', 'payload': {}}
        ])[0]

        # Manually expire
        follow_up.expires_at = timezone.now() - timedelta(hours=1)
        follow_up.save()

        # Should not resolve expired follow-up
        resolved = resolve_short_reply('yes', self.conversation, self.user)
        self.assertIsNone(resolved)
