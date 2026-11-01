"""
Tests for the vision serving path: capability routing, the account-wide
budget, the per-user quota gate, image preparation, and the Cloudflare
Workers AI provider.

These cover the behaviour that was previously missing rather than merely
broken: the tier caps are now enforced, images are analysed once at upload,
and the shared inference budget is protected.
"""

import io
from unittest import mock

from django.test import TestCase, override_settings

from pwanimate.ai.capabilities import (
    model_capability,
    provider_supports_images,
)
from pwanimate.ai.gateway.router import LLMRouter
from pwanimate.ai.gateway.types import AttachmentData, ChatMessage, LLMRequest
from pwanimate.ai.vision_images import image_will_be_sent, prepare_image_for_vision
from pwanimate.billing.global_budget import GlobalBudget
from pwanimate.billing.models import UsageKind
from pwanimate.billing.services import QuotaService, UsageService
from pwanimate.models import PwanimateAttachment
from users.models import User


# --------------------------------------------------------------------- helpers
class FakeRedis:
    """Just enough Redis for the budget: incrby/expire/get/decrby."""

    def __init__(self):
        self.store = {}
        self.expires = {}

    def get(self, key):
        return self.store.get(key)

    def incrby(self, key, amount):
        self.store[key] = int(self.store.get(key, 0)) + int(amount)
        return self.store[key]

    def decrby(self, key, amount):
        self.store[key] = int(self.store.get(key, 0)) - int(amount)
        return self.store[key]

    def expire(self, key, ttl):
        self.expires[key] = ttl
        return True

    def delete(self, key):
        self.store.pop(key, None)
        return True

    def pipeline(self):
        return FakePipeline(self)


class FakePipeline:
    def __init__(self, client):
        self.client = client
        self.ops = []

    def incrby(self, key, amount):
        self.ops.append(("incrby", key, amount))
        return self

    def expire(self, key, ttl):
        self.ops.append(("expire", key, ttl))
        return self

    def execute(self):
        results = []
        for op in self.ops:
            if op[0] == "incrby":
                results.append(self.client.incrby(op[1], op[2]))
            else:
                results.append(self.client.expire(op[1], op[2]))
        return results


def make_image(width=900, height=600, exif=None):
    from PIL import Image

    image = Image.new("RGB", (width, height), "white")
    if exif is not None:
        image.info["exif"] = exif
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=95)
    return buffer.getvalue()


# ---------------------------------------------------------------- capabilities
class CapabilityTableTests(TestCase):
    """Capability is a table, not a literal in the router."""

    def test_gemini_flash_handles_images(self):
        self.assertTrue(provider_supports_images("gemini", "gemini-3.8-flash", 3))

    def test_groq_vision_model_is_capped_at_three_images(self):
        self.assertTrue(provider_supports_images("groq", "qwen/qwen3.8-27b", 3))
        self.assertFalse(provider_supports_images("groq", "qwen/qwen3.8-27b", 4))

    def test_groq_text_model_handles_no_images(self):
        self.assertFalse(provider_supports_images("groq", "openai/gpt-oss-120b", 1))

    def test_openrouter_handles_no_images(self):
        self.assertFalse(provider_supports_images("openrouter", "nvidia/nemotron-3:free", 1))

    @override_settings(
        PWANIMATE_VISION_CAPABLE_MODELS={
            "cloudflare": ["@cf/meta/llama-3.2-11b-vision-instruct"],
        }
    )
    def test_cloudflare_vision_model_handles_images(self):
        self.assertTrue(
            provider_supports_images(
                "cloudflare", "@cf/meta/llama-3.2-11b-vision-instruct", 4
            )
        )

    @override_settings(
        PWANIMATE_VISION_CAPABLE_MODELS={
            "cloudflare": ["@cf/meta/llama-3.2-11b-vision-instruct"],
        }
    )
    def test_unlisted_cloudflare_model_is_text_only(self):
        """An unlisted model must be assumed text-only, not assumed capable."""
        self.assertFalse(
            provider_supports_images("cloudflare", "@cf/meta/gpt-oss-120b", 1)
        )

    def test_unknown_provider_handles_no_images(self):
        self.assertFalse(provider_supports_images("mystery", "some-model", 1))

    def test_zero_images_is_always_acceptable(self):
        self.assertTrue(model_capability("openrouter", "nvidia/x:free").accepts_images(0))


class RouterCapabilityTests(TestCase):
    """The router must admit a cloudflare vision candidate for an image turn."""

    def _router(self, include_cloudflare=False):
        from pwanimate.ai.providers.mock import MockLLMProvider

        providers = {"mock": MockLLMProvider()}
        if include_cloudflare:
            from pwanimate.ai.providers.cloudflare import CloudflareLLMProvider

            providers["cloudflare"] = CloudflareLLMProvider(
                api_token="test-token", account_id="test-account"
            )
        return LLMRouter(providers=providers)

    def test_image_turn_excludes_text_only_candidates(self):
        from pwanimate.ai.exceptions import AIProviderConfigurationError

        router = self._router()
        request = LLMRequest(
            messages=[ChatMessage(role="user", content="describe")],
            metadata={"required_capability": "image", "fallback_chain": [
                {"provider": "openrouter", "model": "nvidia/nemotron-3:free"},
            ]},
        )
        # Every candidate was filtered out, so there is nobody to serve it.
        with self.assertRaises(AIProviderConfigurationError):
            router.resolve_fallback_chain(request)

    def test_cloudflare_vision_candidate_survives_the_filter(self):
        router = self._router(include_cloudflare=True)
        request = LLMRequest(
            messages=[ChatMessage(role="user", content="describe")],
            metadata={
                "required_capability": "image",
                "fallback_chain": [
                    {"provider": "cloudflare", "model": "@cf/meta/llama-3.2-11b-vision-instruct"},
                ],
            },
        )
        chain = router.resolve_fallback_chain(request)
        self.assertEqual(len(chain), 1)
        self.assertEqual(chain[0]["provider"], "cloudflare")


# --------------------------------------------------------------------- budget
class GlobalBudgetTests(TestCase):
    """The account-wide budget must be atomic and must fail open."""

    def test_consume_stops_at_the_limit(self):
        fake = FakeRedis()
        with mock.patch("pwaninet.redis_client.get_redis_client", return_value=fake):
            budget = GlobalBudget("test-limit", 2)
            self.assertTrue(budget.consume(1))
            self.assertTrue(budget.consume(1))
            self.assertFalse(budget.consume(1))
            self.assertEqual(budget.used(), 2)
            self.assertEqual(budget.remaining(), 0)
            self.assertTrue(budget.exhausted())

    def test_overage_is_returned(self):
        """A refused claim must not leave the counter inflated."""
        fake = FakeRedis()
        with mock.patch("pwaninet.redis_client.get_redis_client", return_value=fake):
            budget = GlobalBudget("test-overage", 1)
            self.assertTrue(budget.consume(1))
            self.assertFalse(budget.consume(1))
            self.assertEqual(budget.used(), 1)

    def test_unlimited_budget_never_blocks(self):
        budget = GlobalBudget("test-unlimited", None)
        self.assertTrue(budget.consume(1))
        self.assertTrue(budget.consume(1000))
        self.assertIsNone(budget.remaining())
        self.assertFalse(budget.exhausted())

    def test_redis_failure_fails_open(self):
        """A budget that cannot be read must not take the product down."""
        with mock.patch(
            "pwaninet.redis_client.get_redis_client",
            side_effect=RuntimeError("redis is down"),
        ):
            budget = GlobalBudget("test-down", 1)
            self.assertTrue(budget.consume(1))
            self.assertEqual(budget.used(), 0)
            self.assertFalse(budget.exhausted())


# ---------------------------------------------------------------------- quota
class QuotaGateTests(TestCase):
    """The tier caps must actually stop a turn."""

    def setUp(self):
        self.user = User.objects.create_user(
            username="quota_gate_user", email="quota_gate@vasto.local", password="pass"
        )

    def _tier(self, chats=1, images=0):
        from pwanimate.billing.models import Tier

        return Tier.objects.create(
            code=f"tier-{chats}-{images}-{self.user.pk}",
            name="Test",
            rank=0,
            price_kes=0,
            duration_days=0,
            entitlements={
                "chats": {"day": chats, "week": chats},
                "images": {"day": images},
                "premium_turns": {"day": 0, "period": 0},
            },
            is_default=True,
            is_active=True,
        )

    @override_settings(PWANIMATE_EMBEDDING_PROVIDER="mock")
    def test_check_turn_allows_within_the_allowance(self):
        tier = self._tier(chats=2, images=5)
        UsageService.record_turn(user=self.user, kind=UsageKind.CHAT)
        decision = QuotaService.check_turn(self.user)
        self.assertIsNone(decision, "one chat of two should be allowed")

    @override_settings(PWANIMATE_EMBEDDING_PROVIDER="mock")
    def test_check_turn_blocks_once_the_chat_cap_is_reached(self):
        self._tier(chats=1, images=5)
        UsageService.record_turn(user=self.user, kind=UsageKind.CHAT)
        decision = QuotaService.check_turn(self.user)
        self.assertIsNotNone(decision)
        self.assertFalse(decision.allowed)
        self.assertEqual(decision.entitlement_key, "chats")

    @override_settings(PWANIMATE_EMBEDDING_PROVIDER="mock")
    def test_image_cap_blocks_only_when_images_are_present(self):
        self._tier(chats=20, images=1)
        UsageService.record_turn(user=self.user, images=1, kind=UsageKind.IMAGE)
        self.assertIsNone(
            QuotaService.check_turn(self.user, image_count=0),
            "a text turn must not be blocked by the image cap",
        )
        decision = QuotaService.check_turn(self.user, image_count=1)
        self.assertIsNotNone(decision)
        self.assertEqual(decision.entitlement_key, "images")

    @override_settings(PWANIMATE_EMBEDDING_PROVIDER="mock")
    def test_image_analysis_does_not_spend_a_chat(self):
        """An upload-time analysis spends an image, not a conversation turn."""
        self._tier(chats=1, images=5)
        UsageService.record_image_analysis(user=self.user, provider="mock", model_id="m")
        # The chat allowance must still be untouched.
        decision = QuotaService.check(self.user, kind=UsageKind.CHAT)
        self.assertEqual(decision.used.get("day"), 0)
        # The image allowance must have been spent.
        image_decision = QuotaService.check(self.user, kind=UsageKind.IMAGE)
        self.assertEqual(image_decision.used.get("day"), 1)


# ------------------------------------------------------------- image handling
class ImageHandlingTests(TestCase):
    """Only images that actually reach a provider may be sent or metered."""

    def _attachment(self, **kwargs):
        defaults = {
            "user": None,
            "file_name": "photo.jpg",
            "mime_type": "image/jpeg",
            "attachment_type": "image",
            "processing_status": "ready",
            "vision_summary": "",
            "vision_error": "",
            "vision_context_policy": "ask",
        }
        defaults.update(kwargs)
        return PwanimateAttachment(**defaults)

    def test_analysed_image_is_not_sent_again(self):
        attachment = self._attachment(vision_summary="A timetable with two rows.")
        self.assertFalse(image_will_be_sent(attachment))

    def test_unanalysed_image_is_sent(self):
        self.assertTrue(image_will_be_sent(self._attachment()))

    def test_dropped_image_is_not_sent(self):
        attachment = self._attachment(
            vision_error="Spark Vision is down.", vision_context_policy="drop"
        )
        self.assertFalse(image_will_be_sent(attachment))

    def test_kept_image_is_sent(self):
        attachment = self._attachment(
            vision_error="Spark Vision is down.", vision_context_policy="keep"
        )
        self.assertTrue(image_will_be_sent(attachment))

    def test_image_still_being_analysed_is_not_sent(self):
        """Sending it would analyse it twice and bill it twice."""
        for status in ("pending", "processing"):
            attachment = self._attachment(processing_status=status)
            self.assertFalse(image_will_be_sent(attachment), status)

    def test_document_is_never_an_image(self):
        self.assertFalse(image_will_be_sent(self._attachment(attachment_type="document")))


class ImagePreparationTests(TestCase):
    """Images are downscaled and stripped of metadata before they leave."""

    def test_large_image_is_downscaled(self):
        data = make_image(2400, 1800)
        prepared, mime = prepare_image_for_vision(data, "image/jpeg", max_edge=1024)
        from PIL import Image

        with Image.open(io.BytesIO(prepared)) as image:
            self.assertLessEqual(max(image.size), 1024)
        self.assertEqual(mime, "image/jpeg")

    def test_exif_is_removed(self):
        data = make_image(600, 400, exif=b"Exif\x00\x00secret-gps")
        prepared, _mime = prepare_image_for_vision(data, "image/jpeg", max_edge=1024)
        self.assertNotIn(b"secret-gps", prepared)

    def test_small_image_is_left_alone(self):
        data = make_image(300, 200)
        prepared, _mime = prepare_image_for_vision(data, "image/jpeg", max_edge=1024)
        self.assertEqual(prepared, data)

    def test_unreadable_bytes_are_passed_through(self):
        """Preparation must never be able to fail a turn."""
        prepared, mime = prepare_image_for_vision(b"not an image", "image/jpeg")
        self.assertEqual(prepared, b"not an image")
        self.assertEqual(mime, "image/jpeg")

    def test_empty_bytes_are_passed_through(self):
        prepared, mime = prepare_image_for_vision(b"", "image/jpeg")
        self.assertEqual(prepared, b"")
        self.assertEqual(mime, "image/jpeg")


# ------------------------------------------------------------------ provider
class CloudflareProviderTests(TestCase):
    """The Cloudflare provider must not fire a request it cannot make."""

    def _provider(self, token="", account=""):
        from pwanimate.ai.providers.cloudflare import CloudflareLLMProvider

        return CloudflareLLMProvider(api_token=token, account_id=account)

    def test_not_available_without_credentials(self):
        self.assertFalse(self._provider().is_available())

    def test_available_with_credentials(self):
        self.assertTrue(self._provider(token="t", account="a").is_available())

    def test_endpoint_is_built_correctly(self):
        provider = self._provider(token="t", account="abc")
        url = provider._endpoint("@cf/meta/llama-3.2-11b-vision-instruct")
        self.assertEqual(
            url,
            "https://api.cloudflare.com/client/v4/accounts/abc/ai/run/"
            "@cf/meta/llama-3.2-11b-vision-instruct",
        )

    def test_payload_carries_image_parts(self):
        provider = self._provider(token="t", account="abc")
        attachment = AttachmentData(
            id="img-1",
            name="photo.jpg",
            mime_type="image/jpeg",
            attachment_type="image",
            data_bytes=make_image(200, 200),
        )
        request = LLMRequest(
            messages=[ChatMessage(role="user", content="what is this", attachments=[attachment])],
            max_tokens=100,
        )
        payload = provider._build_payload(request)
        parts = payload["messages"][0]["content"]
        self.assertEqual(parts[0]["type"], "text")
        self.assertEqual(parts[1]["type"], "image_url")
        self.assertTrue(parts[1]["image_url"]["url"].startswith("data:image/"))

    def test_licence_agreement_is_surfaced_clearly(self):
        """A model that needs its licence accepted must say so plainly."""
        from pwanimate.ai.exceptions import AIProviderAuthenticationError

        provider = self._provider(token="t", account="abc")
        response = mock.Mock(status_code=403, text="Model Agreement: you must agree")
        with self.assertRaises(AIProviderAuthenticationError) as ctx:
            provider._parse_response(response, 0.1)
        self.assertIn("agree", str(ctx.exception).lower())

    def test_rate_limit_raises_the_rate_limit_error(self):
        from pwanimate.ai.exceptions import AIProviderRateLimitError

        provider = self._provider(token="t", account="abc")
        response = mock.Mock(status_code=429, text="", headers={"Retry-After": "30"})
        with self.assertRaises(AIProviderRateLimitError):
            provider._parse_response(response, 0.1)

    def test_empty_response_is_an_error(self):
        from pwanimate.ai.exceptions import AIProviderAPIError

        provider = self._provider(token="t", account="abc")
        response = mock.Mock(
            status_code=200,
            json=lambda: {"success": True, "result": {"response": "", "usage": {}}},
        )
        with self.assertRaises(AIProviderAPIError):
            provider._parse_response(response, 0.1)

    def test_neurons_are_reported_and_costed(self):
        provider = self._provider(token="t", account="abc")
        response = mock.Mock(
            status_code=200,
            json=lambda: {
                "success": True,
                "result": {
                    "response": "a timetable",
                    "usage": {"prompt_tokens": 100, "completion_tokens": 10, "neurons": 35.0},
                },
            },
        )
        parsed = provider._parse_response(response, 0.1)
        self.assertEqual(parsed.content, "a timetable")
        self.assertEqual(parsed.prompt_tokens, 100)
        self.assertAlmostEqual(parsed.metadata["neurons"], 35.0)
        self.assertGreater(parsed.metadata["estimated_cost_usd"], 0)
