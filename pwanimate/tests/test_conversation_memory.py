from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase

from pwanimate.models import PwanimateConversation, PwanimateMessage
from pwanimate.services.conversation_memory import ConversationMemoryService

User = get_user_model()


class ConversationMemoryServiceTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="memory_student",
            email="memory_student@vasto.local",
            password="test-password",
        )
        self.other_user = User.objects.create_user(
            username="other_memory_student",
            email="other_memory_student@vasto.local",
            password="test-password",
        )

    def test_approved_fact_recall_is_owner_scoped_across_conversations(self):
        old_conversation = PwanimateConversation.objects.create(
            user=self.user,
            title="Database revision",
        )
        source = PwanimateMessage.objects.create(
            conversation=old_conversation,
            role="user",
            content="We studied database normalization and functional dependencies.",
        )
        other_conversation = PwanimateConversation.objects.create(
            user=self.other_user,
            title="Private database notes",
        )
        PwanimateMessage.objects.create(
            conversation=other_conversation,
            role="user",
            content="We studied database normalization and functional dependencies.",
        )
        active_conversation = PwanimateConversation.objects.create(user=self.user, title="Current")
        PwanimateMessage.objects.create(
            conversation=active_conversation,
            role="user",
            content="Database normalization in the current thread.",
        )

        from pwanimate.services.personal_memory import PersonalMemoryService
        PersonalMemoryService.set_enabled(self.user, True)
        fact = PersonalMemoryService.propose(self.user, source.content, source.pk)
        PersonalMemoryService.change(self.user, fact.pk, 'approve', fact.version)
        with patch(
            "pwanimate.services.conversation_memory.get_embedding_provider",
            side_effect=RuntimeError("embedding service unavailable"),
        ):
            results = ConversationMemoryService.search(
                self.user,
                "What did we discuss about database normalization?",
                exclude_conversation_id=active_conversation.id,
            )

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["conversation_id"], str(old_conversation.id))
        self.assertEqual(results[0]["match_type"], "persistent_fact")

    def test_search_returns_empty_for_anonymous_users(self):
        self.assertEqual(
            ConversationMemoryService.search(
                type("Anonymous", (), {"is_authenticated": False})(),
                "What did we discuss?",
            ),
            [],
        )

    def test_raw_transcripts_are_not_implicitly_imported(self):
        from pwanimate.services.personal_memory import PersonalMemoryService
        PersonalMemoryService.set_enabled(self.user, True)
        conversation = PwanimateConversation.objects.create(user=self.user)
        PwanimateMessage.objects.create(conversation=conversation, role='user', content='I struggle with calculus')
        self.assertEqual(ConversationMemoryService.search(self.user, 'calculus'), [])
