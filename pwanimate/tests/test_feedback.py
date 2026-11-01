"""
Tests for the answer-feedback signal.

The platform records what was served and what it cost. These tests cover the
part that was missing: whether the answer was right, and which model
produced it.
"""

from django.test import TestCase

import json

from pwanimate.models import PwanimateConversation, PwanimateMessage
from pwanimate.services.conversation import ConversationService
from pwanimate.services.feedback import FeedbackError, FeedbackService
from users.models import User


class FeedbackServiceTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(
            username="feedback_owner", email="fb_owner@vasto.local", password="pass"
        )
        self.other = User.objects.create_user(
            username="feedback_other", email="fb_other@vasto.local", password="pass"
        )
        self.conversation = PwanimateConversation.objects.create(user=self.owner)
        self.answer = ConversationService.persist_assistant_message(
            conversation=self.conversation,
            content="The lecture is at 08:00 on Monday.",
            provider="cloudflare",
            model_id="@cf/meta/llama-3.2-11b-vision-instruct",
            task="rag",
            prompt_tokens=120,
            completion_tokens=30,
            total_tokens=150,
        )

    def test_serving_metadata_is_persisted(self):
        """The answer must record who produced it -- that is what makes
        "is the new model better?" answerable."""
        message = PwanimateMessage.objects.get(id=self.answer.id)
        self.assertEqual(message.provider, "cloudflare")
        self.assertEqual(message.model_id, "@cf/meta/llama-3.2-11b-vision-instruct")
        self.assertEqual(message.task, "rag")
        self.assertEqual(message.total_tokens, 150)

    def test_recording_a_verdict(self):
        result = FeedbackService.record(
            user=self.owner, message_id=self.answer.id, helpful=True
        )
        self.assertTrue(result["helpful"])
        self.assertIsNotNone(result["id"])

    def test_a_student_can_change_their_mind(self):
        """One verdict per student per answer; the last one wins."""
        FeedbackService.record(
            user=self.owner, message_id=self.answer.id, helpful=True
        )
        FeedbackService.record(
            user=self.owner,
            message_id=self.answer.id,
            helpful=False,
            reason="wrong_answer",
        )
        stored = FeedbackService.get_for_message(self.owner, self.answer.id)
        self.assertFalse(stored["helpful"])
        self.assertEqual(stored["reason"], "wrong_answer")

    def test_cannot_rate_someone_elses_answer(self):
        with self.assertRaises(FeedbackError):
            FeedbackService.record(
                user=self.other, message_id=self.answer.id, helpful=True
            )

    def test_cannot_rate_a_user_message(self):
        user_message = ConversationService.persist_user_message(
            self.conversation, "hello"
        )
        with self.assertRaises(FeedbackError):
            FeedbackService.record(
                user=self.owner, message_id=user_message.id, helpful=True
            )

    def test_an_unknown_reason_is_refused(self):
        with self.assertRaises(FeedbackError):
            FeedbackService.record(
                user=self.owner,
                message_id=self.answer.id,
                helpful=False,
                reason="because-i-said-so",
            )

    def test_summary_groups_by_model(self):
        """The query that decides whether a model change helped."""
        FeedbackService.record(
            user=self.owner, message_id=self.answer.id, helpful=True
        )
        summary = FeedbackService.summary()
        self.assertEqual(summary["total"], 1)
        self.assertEqual(summary["helpful"], 1)
        self.assertEqual(summary["helpful_rate"], 1.0)
        self.assertEqual(len(summary["by_model"]), 1)
        entry = summary["by_model"][0]
        self.assertEqual(entry["provider"], "cloudflare")
        self.assertEqual(entry["helpful_rate"], 1.0)


class FeedbackApiTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="feedback_api", email="fb_api@vasto.local", password="pass"
        )
        self.conversation = PwanimateConversation.objects.create(user=self.user)
        self.answer = ConversationService.persist_assistant_message(
            conversation=self.conversation, content="An answer."
        )

    def _post(self, **payload):
        self.client.force_login(self.user)
        return self.client.post(
            f"/api/pwanimate/messages/{self.answer.id}/feedback/",
            data=json.dumps(payload),
            content_type="application/json",
        )

    def test_posting_feedback(self):
        response = self._post(helpful=True)
        self.assertEqual(response.status_code, 201)
        self.assertTrue(response.json()["feedback"]["helpful"])

    def test_feedback_requires_a_verdict(self):
        response = self._post()
        self.assertEqual(response.status_code, 400)

    def test_reading_back_the_verdict(self):
        self._post(helpful=False, reason="wrong_answer")
        self.client.force_login(self.user)
        response = self.client.get(
            f"/api/pwanimate/messages/{self.answer.id}/feedback/"
        )
        self.assertEqual(response.status_code, 200)
        feedback = response.json()["feedback"]
        self.assertFalse(feedback["helpful"])
        self.assertEqual(feedback["reason"], "wrong_answer")

    def test_feedback_requires_authentication(self):
        self.client.logout()
        response = self.client.post(
            f"/api/pwanimate/messages/{self.answer.id}/feedback/",
            data=json.dumps({"helpful": True}),
            content_type="application/json",
        )
        self.assertIn(
            response.status_code, [401, 403]
        )

    def test_summary_is_staff_only(self):
        self.client.force_login(self.user)
        response = self.client.get("/api/pwanimate/feedback/summary/")
        self.assertIn(response.status_code, [401, 403])
