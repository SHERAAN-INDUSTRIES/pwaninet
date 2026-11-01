"""
Tests for capability-aware routing.

A turn that carries an image may only be served by an image-capable model.
The capability is INFERRED from the request (the router counts the image
attachments), not taken on trust from a metadata flag — the tool path used to
omit the flag and handed an image to a text-only model, which then refused
the whole turn.
"""

from types import SimpleNamespace

from django.test import SimpleTestCase

from pwanimate.ai.gateway.router import LLMRouter
from pwanimate.ai.gateway.types import ChatMessage, LLMRequest


def _image_attachment(att_id="img-1"):
    return SimpleNamespace(id=att_id, attachment_type="image", file_name="photo.png")


class CapabilityRoutingTests(SimpleTestCase):

    def _router(self):
        from pwanimate.ai.providers import MockLLMProvider

        return LLMRouter(
            providers={
                "mock": MockLLMProvider(),
                "gemini": MockLLMProvider(),
                "groq": MockLLMProvider(),
                "openrouter": MockLLMProvider(),
            },
            default_provider="mock",
        )

    def _chain(self, attachments=None, message_attachments=None, chain=None):
        chain = chain or [
            {"provider": "gemini", "model": "gemini-3.8-flash"},
            {"provider": "groq", "model": "openai/gpt-oss-120b"},
            {"provider": "groq", "model": "qwen/qwen3.8-27b"},
            {"provider": "openrouter", "model": "nvidia/nemotron-3-super-120b-a12b:free"},
        ]
        message = ChatMessage(
            role="user",
            content="what is in this picture?",
            attachments=message_attachments or [],
        )
        request = LLMRequest(
            messages=[message],
            provider="gemini",
            model="gemini-3.8-flash",
            attachments=attachments or [],
            metadata={"fallback_chain": chain},
        )
        return self._router().resolve_fallback_chain(request)

    def test_image_turn_excludes_text_only_models(self):
        candidates = self._chain(attachments=[_image_attachment()])
        models = [c["model"] for c in candidates]
        # gpt-oss-120b and the nemotron text model cannot read images.
        self.assertNotIn("openai/gpt-oss-120b", models)
        self.assertNotIn("nvidia/nemotron-3-super-120b-a12b:free", models)
        # The image-capable rungs survive, in order.
        self.assertIn("gemini-3.8-flash", models)
        self.assertIn("qwen/qwen3.8-27b", models)
        self.assertEqual(models[0], "gemini-3.8-flash")

    def test_capability_is_inferred_without_a_metadata_flag(self):
        """No `required_capability` in the metadata — the router still filters."""
        candidates = self._chain(attachments=[_image_attachment()])
        self.assertNotIn("openai/gpt-oss-120b", [c["model"] for c in candidates])

    def test_image_on_the_message_is_also_detected(self):
        candidates = self._chain(message_attachments=[_image_attachment()])
        self.assertNotIn("openai/gpt-oss-120b", [c["model"] for c in candidates])

    def test_text_only_turn_keeps_the_whole_ladder(self):
        candidates = self._chain()
        models = [c["model"] for c in candidates]
        self.assertIn("openai/gpt-oss-120b", models)
        self.assertIn("nvidia/nemotron-3-super-120b-a12b:free", models)

    def test_groq_vision_model_is_limited_to_three_images(self):
        many = [_image_attachment(f"img-{index}") for index in range(4)]
        candidates = self._chain(attachments=many)
        models = [c["model"] for c in candidates]
        # Groq's Qwen vision model accepts at most three images per request.
        self.assertNotIn("qwen/qwen3.8-27b", models)
        self.assertIn("gemini-3.8-flash", models)
