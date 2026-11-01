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

    def test_lexical_fallback_is_owner_scoped_and_excludes_active_conversation(self):
        old_conversation = PwanimateConversation.objects.create(
            user=self.user,
            title="Database revision",
        )
        PwanimateMessage.objects.create(
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
        self.assertEqual(results[0]["match_type"], "lexical")

    def test_search_returns_empty_for_anonymous_users(self):
        self.assertEqual(
            ConversationMemoryService.search(
                type("Anonymous", (), {"is_authenticated": False})(),
                "What did we discuss?",
            ),
            [],
        )
