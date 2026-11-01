"""
Tests for the one-time image analysis.

An uploaded image is analysed ONCE (at upload, by the attachment task) and
the description is stored. Every later turn reuses that description as
context, so a photo is not re-analysed for each follow-up question.
"""

from types import SimpleNamespace
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase

from pwanimate.models import PwanimateAttachment


class ImageAnalysisOnceTests(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_user(
            username="image_student", email="image@vasto.local", password="testpassword123"
        )

    def _image_attachment(self, name="photo.png"):
        return PwanimateAttachment.objects.create(
            user=self.user,
            file=SimpleUploadedFile(name, b"\x89PNG\r\n\x1a\n" + b"0" * 64, content_type="image/png"),
            file_name=name,
            file_size=72,
            mime_type="image/png",
            attachment_type="image",
            processing_status="pending",
        )

    def test_upload_analyses_the_image_once_and_stores_it(self):
        from pwanimate.ai.gateway.types import LLMResponse
        from pwanimate.tasks.attachments import process_pwanimate_attachment

        attachment = self._image_attachment()
        fake_response = LLMResponse(
            content="A bar chart of rainfall by month.",
            provider="gemini",
            model="gemini-3.8-flash",
            finish_reason="stop",
            prompt_tokens=100,
            completion_tokens=20,
            total_tokens=120,
        )
        with mock.patch(
            "pwanimate.tasks.attachments.AIGateway"
        ) as gateway_cls:
            gateway_cls.return_value.generate.return_value = fake_response
            result = process_pwanimate_attachment(str(attachment.id))

        self.assertEqual(result["status"], "ready")
        attachment.refresh_from_db()
        self.assertEqual(attachment.vision_summary, "A bar chart of rainfall by month.")
        self.assertEqual(attachment.vision_model, "gemini:gemini-3.8-flash")
        self.assertEqual(attachment.processing_status, "ready")
        # Exactly one vision call for the upload.
        self.assertEqual(gateway_cls.return_value.generate.call_count, 1)

    def test_an_already_analysed_image_is_not_analysed_again(self):
        from pwanimate.tasks.attachments import process_pwanimate_attachment

        attachment = self._image_attachment()
        attachment.vision_summary = "Already described."
        attachment.processing_status = "ready"
        attachment.save(update_fields=["vision_summary", "processing_status"])

        with mock.patch("pwanimate.tasks.attachments.AIGateway") as gateway_cls:
            result = process_pwanimate_attachment(str(attachment.id))

        self.assertEqual(result["status"], "ready")
        gateway_cls.return_value.generate.assert_not_called()

    def test_a_failed_analysis_is_recorded_not_raised(self):
        from pwanimate.ai.exceptions import AIProviderRateLimitError
        from pwanimate.tasks.attachments import process_pwanimate_attachment

        attachment = self._image_attachment()
        with mock.patch("pwanimate.tasks.attachments.AIGateway") as gateway_cls:
            gateway_cls.return_value.generate.side_effect = AIProviderRateLimitError(
                "quota", provider="gemini"
            )
            result = process_pwanimate_attachment(str(attachment.id))

        self.assertEqual(result["status"], "failed")
        attachment.refresh_from_db()
        self.assertEqual(attachment.processing_status, "failed")
        self.assertEqual(attachment.vision_summary, "")

    def test_later_turns_reuse_the_stored_description(self):
        """The chat must not re-send the image when a description exists."""
        from pwanimate.orchestrator import PwanimateOrchestrator

        attachment = SimpleNamespace(
            id="att-1",
            file_name="photo.png",
            mime_type="image/png",
            attachment_type="image",
            vision_summary="A bar chart of rainfall by month.",
            processing_status="ready",
            file=None,  # no bytes available — the point: we must not need them
        )
        images, context = PwanimateOrchestrator._process_attachments(
            SimpleNamespace(),
            [attachment],
            query="what does the chart show?",
        )
        # No image is attached to the model request: the description is the context.
        self.assertEqual(images, [])
        self.assertIn("A bar chart of rainfall by month.", context)
        self.assertIn("IMAGE ANALYSIS", context)

    def test_without_a_stored_description_the_image_is_still_sent(self):
        """Legacy uploads (no analysis) keep working: the image goes through."""
        from pwanimate.orchestrator import PwanimateOrchestrator

        attachment = SimpleNamespace(
            id="att-2",
            file_name="photo.png",
            mime_type="image/png",
            attachment_type="image",
            vision_summary="",
            processing_status="ready",
            data_bytes=b"\x89PNG",
        )
        images, context = PwanimateOrchestrator._process_attachments(
            SimpleNamespace(),
            [attachment],
            query="what is this?",
        )
        self.assertEqual(len(images), 1)
        self.assertEqual(images[0].data_bytes, b"\x89PNG")
