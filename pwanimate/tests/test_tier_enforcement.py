"""
Tests for tier enforcement at the request path.

The rule under test: a candidate the caller's tier may not reach is withheld
from the resolved chain, so a free-tier student can never fall through to a
paid model (the frontier rung in the global Auto chain is the live example).
Also: an explicitly requested offering is a registry entry, locked offerings
are refused with the upgrade path, and the tiers API tells the UI which
premium capability the caller may use and how much of it is left.
"""

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone

from pwanimate.ai.gateway.router import LLMRouter
from pwanimate.ai.gateway.types import ChatMessage, LLMRequest
from pwanimate.ai.providers import MockLLMProvider
from pwanimate.billing.models import ModelOffering, Subscription, Tier
from pwanimate.billing.services import QuotaService

FRONTIER_MODEL = "gemini-pro-latest"


class TierEnforcementTestCase(TestCase):
    @classmethod
    def setUpTestData(cls):
        from io import StringIO

        call_command("seed_model_registry", verbosity=0, stdout=StringIO())
        cls.user = get_user_model().objects.create_user(
            username="tier_student", email="tier@vasto.local", password="testpassword123"
        )
        cls.router = LLMRouter(
            providers={
                "mock": MockLLMProvider(),
                "gemini": MockLLMProvider(),
                "groq": MockLLMProvider(),
            },
            default_provider="mock",
        )
        cls.chain = [
            {"provider": "gemini", "model": "gemini-3.8-flash"},
            {"provider": "groq", "model": "openai/gpt-oss-120b"},
            {"provider": "gemini", "model": FRONTIER_MODEL},
        ]

    def _request(self, tier_rank, offering=None):
        metadata = {"fallback_chain": list(self.chain)}
        if tier_rank is not None:
            metadata["tier_rank"] = tier_rank
        if offering:
            metadata["offering"] = offering
        return LLMRequest(
            messages=[ChatMessage(role="user", content="hi")],
            provider="gemini",
            model="gemini-3.8-flash",
            metadata=metadata,
        )

    def test_free_tier_never_reaches_the_frontier_model(self):
        candidates = self.router.resolve_fallback_chain(self._request(tier_rank=0))
        models = [c["model"] for c in candidates]
        self.assertNotIn(FRONTIER_MODEL, models)
        self.assertIn("gemini-3.8-flash", models)
        self.assertIn("openai/gpt-oss-120b", models)

    def test_paid_tier_keeps_the_frontier_model_as_a_last_resort(self):
        candidates = self.router.resolve_fallback_chain(self._request(tier_rank=2))
        models = [c["model"] for c in candidates]
        self.assertIn(FRONTIER_MODEL, models)
        # Free routes still lead — the paid model is a fallback, not the default.
        self.assertEqual(models[0], "gemini-3.8-flash")

    def test_no_tier_context_means_no_filtering(self):
        """Direct gateway use (no tier in the request) keeps the whole chain."""
        candidates = self.router.resolve_fallback_chain(self._request(tier_rank=None))
        self.assertIn(FRONTIER_MODEL, [c["model"] for c in candidates])

    def test_requested_offering_is_served_first_for_an_allowed_tier(self):
        candidates = self.router.resolve_fallback_chain(
            self._request(tier_rank=2, offering="frontier-pro")
        )
        self.assertEqual(candidates[0]["provider"], "gemini")
        self.assertEqual(candidates[0]["model"], FRONTIER_MODEL)

    def test_requested_offering_is_not_served_to_a_locked_tier(self):
        candidates = self.router.resolve_fallback_chain(
            self._request(tier_rank=0, offering="frontier-pro")
        )
        self.assertNotEqual(candidates[0]["model"], FRONTIER_MODEL)

    def test_unregistered_model_is_withheld_from_the_free_tier(self):
        """An unpriced model must not become a free-tier leak."""
        request = LLMRequest(
            messages=[ChatMessage(role="user", content="hi")],
            provider="gemini",
            model="gemini-3.8-flash",
            metadata={
                "tier_rank": 0,
                "fallback_chain": [
                    {"provider": "gemini", "model": "gemini-3.8-flash"},
                    {"provider": "groq", "model": "mystery-paid-model"},
                ],
            },
        )
        candidates = self.router.resolve_fallback_chain(request)
        self.assertNotIn("mystery-paid-model", [c["model"] for c in candidates])


class TierApiEnforcementTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        from io import StringIO

        call_command("seed_model_registry", verbosity=0, stdout=StringIO())
        cls.user = get_user_model().objects.create_user(
            username="tier_api_student", email="tierapi@vasto.local", password="testpassword123"
        )

    def setUp(self):
        from rest_framework.test import APIClient

        self.client = APIClient()
        self.client.force_authenticate(user=self.user)

    def test_free_tier_requesting_a_locked_offering_is_refused(self):
        resp = self.client.post(
            "/api/pwanimate/chat/",
            {"message": "hi", "offering": "frontier-pro"},
            format="json",
        )
        self.assertEqual(resp.status_code, 403)
        body = resp.json()
        self.assertEqual(body["error"], "model_locked")
        self.assertEqual(body["required_tier"], "pro_monthly")
        self.assertEqual(body["price_kes"], 300.0)
        self.assertIn("Pro Monthly", body["message"])

    def test_unknown_offering_is_a_bad_request(self):
        resp = self.client.post(
            "/api/pwanimate/chat/",
            {"message": "hi", "offering": "not-a-real-offering"},
            format="json",
        )
        self.assertEqual(resp.status_code, 400)

    def test_tiers_api_exposes_the_premium_capability_for_the_ui(self):
        resp = self.client.get("/api/pwanimate/billing/tiers/")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["current_tier"], "ember")
        premium = {item["key"]: item for item in body["premium"]}
        self.assertIn("frontier-pro", premium)
        # Locked for the free tier, and the UI is told which tier unlocks it.
        self.assertFalse(premium["frontier-pro"]["allowed"])
        self.assertEqual(premium["frontier-pro"]["required_tier"], "pro_monthly")

    def test_tiers_api_shows_the_premium_chip_once_paid(self):
        tier = Tier.objects.get(code="pro_monthly")
        Subscription.objects.create(
            user=self.user, tier=tier, status=Subscription.Status.ACTIVE,
            starts_at=timezone.now() - timezone.timedelta(days=1),
            ends_at=timezone.now() + timezone.timedelta(days=29),
        )
        resp = self.client.get("/api/pwanimate/billing/tiers/")
        body = resp.json()
        premium = {item["key"]: item for item in body["premium"]}
        self.assertTrue(premium["frontier-pro"]["allowed"])
        self.assertEqual(premium["frontier-pro"]["remaining_today"], 3)
        self.assertEqual(body["current_tier"], "pro_monthly")
