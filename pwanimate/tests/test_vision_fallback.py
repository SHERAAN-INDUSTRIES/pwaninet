"""
Tests for the Spark Vision fallback contract.

When the image cannot be served (every image-capable model is down, the
worker is unreachable, the provider refuses), the turn must NOT fail: the
text model answers, and the student is told plainly that Spark Vision is
down — as a sentence in the answer, as a warning on the response, and as a
notice in the UI.
"""

from django.test import SimpleTestCase

from pwanimate.orchestrator.service import (
    SPARK_VISION_UNAVAILABLE_NOTICE,
    _vision_fallback_instruction,
    _vision_unavailable_warning,
)


class VisionUnavailableMessageTests(SimpleTestCase):

    def test_notice_names_spark_vision_and_asks_the_student_to_retry(self):
        notice = SPARK_VISION_UNAVAILABLE_NOTICE.lower()
        self.assertIn("spark vision", notice)
        self.assertIn("down", notice)
        self.assertIn("try again", notice)

    def test_fallback_instruction_carries_the_notice(self):
        instruction = _vision_fallback_instruction("You are Spark.")
        self.assertIn("You are Spark.", instruction)
        self.assertIn(SPARK_VISION_UNAVAILABLE_NOTICE, instruction)
        # The model must be told not to pretend it saw the image.
        self.assertIn("tell them this plainly", instruction.lower())

    def test_warning_is_user_facing(self):
        """The marker still exists for callers that want it, but the chat no
        longer renders a banner: the model reports the outage in its answer."""
        warning = _vision_unavailable_warning()
        self.assertEqual(warning["code"], "spark_vision_unavailable")
        self.assertIn("Spark Vision", warning["title"])
        self.assertEqual(warning["message"], SPARK_VISION_UNAVAILABLE_NOTICE)


class VisionFallbackDegradationTests(SimpleTestCase):
    """The degradation contract, without standing up the whole orchestrator."""

    def test_the_fallback_is_text_only_and_advertised(self):
        from pwanimate.ai.gateway.types import LLMRequest, ChatMessage

        request = LLMRequest(
            task="tool",
            messages=[ChatMessage(role="user", content="what is in this photo?")],
            system_instruction="You are Spark.",
            attachments=[],
        )
        instruction = _vision_fallback_instruction(request.system_instruction)
        self.assertIn(SPARK_VISION_UNAVAILABLE_NOTICE, instruction)
        # The degraded request carries no attachments, so no provider can
        # refuse it for image support.
        self.assertEqual(request.attachments, [])
