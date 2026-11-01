"""
Tests for the image-context policy after a vision failure.

When Spark Vision cannot analyse an uploaded image, the student decides what
happens to it: keep it in the conversation (they may want to ask about it
again) or drop it. The decision is stored, asked once, and honoured on every
later turn.
"""

from types import SimpleNamespace
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from rest_framework.test import APIClient

from pwanimate.models import PwanimateAttachment


def _attachment(policy="ask", summary="", error="Spark Vision is down and didn't respond."):
    return SimpleNamespace(
        id="att-1",
        file_name="photo.png",
        mime_type="image/png",
        attachment_type="image",
        vision_summary=summary,
        vision_error=error,
        vision_context_policy=policy,
        processing_status="ready" if summary else "failed",
        data_bytes=b"\x89PNG",
    )


class VisionPolicyContextTests(TestCase):
    """Which images reach the model, per policy."""

    def _process(self, attachment):
        from pwanimate.orchestrator import PwanimateOrchestrator

        return PwanimateOrchestrator._process_attachments(
            SimpleNamespace(),
            [attachment],
            query="what does this show?",
        )

    def test_dropped_image_is_never_sent_again(self):
        images, context = self._process(_attachment(policy="drop"))
        self.assertEqual(images, [])
        self.assertIn("Spark Vision is down", context)
        self.assertIn("not available", context)

    def test_kept_image_is_sent_again_for_reuse(self):
        images, context = self._process(_attachment(policy="keep"))
        self.assertEqual(len(images), 1)
        self.assertEqual(images[0].data_bytes, b"\x89PNG")

    def test_undecided_image_is_offered_one_more_time(self):
        """'ask' sends it once more (the route may have recovered) and the
        student is offered the choice."""
        images, context = self._process(_attachment(policy="ask"))
        self.assertEqual(len(images), 1)

    def test_a_fully_analysed_image_is_never_resent(self):
        images, context = self._process(
            _attachment(policy="ask", summary="A bar chart of rainfall.", error="")
        )
        self.assertEqual(images, [])
        self.assertIn("A bar chart of rainfall.", context)


class VisionPolicyApiTests(TestCase):

    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(
            username="policy_student", email="policy@vasto.local", password="testpassword123"
        )
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)
        self.attachment = PwanimateAttachment.objects.create(
            user=self.user,
            file=SimpleUploadedFile("photo.png", b"\x89PNG" + b"0" * 32, content_type="image/png"),
            file_name="photo.png",
            file_size=36,
            mime_type="image/png",
            attachment_type="image",
            processing_status="failed",
            vision_error="Spark Vision is down and didn't respond.",
        )

    def test_keep_records_the_choice(self):
        resp = self.client.post(
            f"/api/pwanimate/attachments/{self.attachment.id}/vision-policy/",
            {"policy": "keep"},
            format="json",
        )
        self.assertEqual(resp.status_code, 200)
        self.attachment.refresh_from_db()
        self.assertEqual(self.attachment.vision_context_policy, "keep")
        self.assertIn("Kept", resp.json()["message"])

    def test_drop_records_the_choice(self):
        resp = self.client.post(
            f"/api/pwanimate/attachments/{self.attachment.id}/vision-policy/",
            {"policy": "drop"},
            format="json",
        )
        self.assertEqual(resp.status_code, 200)
        self.attachment.refresh_from_db()
        self.assertEqual(self.attachment.vision_context_policy, "drop")
        self.assertIn("Dropped", resp.json()["message"])

    def test_invalid_policy_is_rejected(self):
        resp = self.client.post(
            f"/api/pwanimate/attachments/{self.attachment.id}/vision-policy/",
            {"policy": "maybe"},
            format="json",
        )
        self.assertEqual(resp.status_code, 400)

    def test_someone_elses_attachment_is_not_found(self):
        other = get_user_model().objects.create_user(
            username="other_student", email="other@vasto.local", password="testpassword123"
        )
        client = APIClient()
        client.force_authenticate(user=other)
        resp = client.post(
            f"/api/pwanimate/attachments/{self.attachment.id}/vision-policy/",
            {"policy": "drop"},
            format="json",
        )
        self.assertEqual(resp.status_code, 404)

    def test_a_failed_analysis_remembers_the_failure(self):
        """The upload task stores the failure so the image is not re-sent."""
        from pwanimate.ai.gateway.types import LLMResponse
        from pwanimate.ai.exceptions import AIProviderRateLimitError
        from pwanimate.tasks.attachments import process_pwanimate_attachment

        with mock.patch("pwanimate.tasks.attachments.AIGateway") as gateway_cls:
            gateway_cls.return_value.generate.side_effect = AIProviderRateLimitError(
                "quota", provider="gemini"
            )
            result = process_pwanimate_attachment(str(self.attachment.id))

        self.assertEqual(result["status"], "failed")
        self.attachment.refresh_from_db()
        self.assertIn("Spark Vision is down", self.attachment.vision_error)
        # The choice is still open.
        self.assertEqual(self.attachment.vision_context_policy, "ask")
