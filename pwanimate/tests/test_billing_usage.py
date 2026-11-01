"""
Tests for the billing backend: usage accounting, quota windows and decisions.

The rules under test:
- a real turn (model + provider token counts) is recorded and rolled into
  the day / week / period counters,
- the day window is local midnight (Africa/Nairobi), the week starts Monday,
- the quota decision distinguishes allowed / queued (degrade, never block)
  and upgrade_required (the feature is not in the tier at all),
- the snapshot the UI renders is built from the same counters the check
  enforces — so the student can never see a number we do not act on.
"""

from datetime import datetime, timedelta
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone

from pwanimate.billing.models import (
    CostClass,
    ModelOffering,
    Subscription,
    Tier,
    UsageCounter,
    UsageRecord,
)
from pwanimate.billing.services import (
    QuotaService,
    TierService,
    UsageService,
    local_day_start,
    local_week_start,
)


class BillingTestCase(TestCase):
    """Shared setup: the seeded catalogue."""

    @classmethod
    def setUpTestData(cls):
        from io import StringIO

        call_command("seed_model_registry", verbosity=0, stdout=StringIO())
        cls.user = get_user_model().objects.create_user(
            username="billing_student", email="billing@vasto.local", password="testpassword123"
        )


class UsageAccountingTests(BillingTestCase):

    def test_record_turn_writes_record_and_counters(self):
        now = timezone.now()
        offering = ModelOffering.objects.get(provider="gemini", model_id="gemini-3.8-flash")
        record = UsageService.record_turn(
            user=self.user,
            provider="gemini",
            model_id="gemini-3.8-flash",
            prompt_tokens=1200,
            completion_tokens=300,
            total_tokens=1500,
            now=now,
        )
        self.assertIsNotNone(record)
        self.assertEqual(record.cost_class, CostClass.FREE_ROUTE)
        self.assertEqual(record.cost_usd_micros, 0)
        self.assertEqual(UsageRecord.objects.filter(user=self.user).count(), 1)

        counters = {
            c.window_type: c
            for c in UsageCounter.objects.filter(user=self.user)
        }
        self.assertIn("day", counters)
        self.assertIn("week", counters)
        self.assertEqual(counters["day"].chats, 1)
        self.assertEqual(counters["day"].prompt_tokens, 1200)
        self.assertEqual(counters["day"].completion_tokens, 300)
        self.assertEqual(counters["week"].chats, 1)
        # A free route costs us nothing.
        self.assertEqual(counters["day"].cost_usd_micros, 0)

    def test_images_are_counted_separately_from_chats(self):
        UsageService.record_turn(
            user=self.user, provider="gemini", model_id="gemini-3.8-flash",
            prompt_tokens=100, completion_tokens=50, images=2, now=timezone.now(),
        )
        counter = UsageCounter.objects.get(
            user=self.user, window_type="day", window_start=local_day_start(timezone.now())
        )
        self.assertEqual(counter.chats, 1)
        self.assertEqual(counter.images, 2)

    def test_paid_offering_costs_real_money(self):
        UsageService.record_turn(
            user=self.user, provider="gemini", model_id="gemini-pro-latest",
            prompt_tokens=1_000_000, completion_tokens=1_000_000, now=timezone.now(),
        )
        counter = UsageCounter.objects.get(
            user=self.user, window_type="day", window_start=local_day_start(timezone.now())
        )
        # 1M in at $1.50 + 1M out at $6.00 = $7.50 = 7,500,000 micros.
        self.assertEqual(counter.cost_usd_micros, 7_500_000)
        self.assertEqual(counter.premium_turns, 1)

    def test_turns_land_in_the_window_they_happened_in(self):
        yesterday = timezone.now() - timedelta(days=1)
        UsageService.record_turn(
            user=self.user, provider="gemini", model_id="gemini-3.8-flash",
            prompt_tokens=10, completion_tokens=5, now=yesterday,
        )
        UsageService.record_turn(
            user=self.user, provider="gemini", model_id="gemini-3.8-flash",
            prompt_tokens=10, completion_tokens=5, now=timezone.now(),
        )
        today = timezone.now()
        counters = {
            c.window_start: c
            for c in UsageCounter.objects.filter(user=self.user, window_type="day")
        }
        self.assertIn(local_day_start(yesterday), counters)
        self.assertIn(local_day_start(today), counters)
        self.assertEqual(counters[local_day_start(yesterday)].chats, 1)
        # Both turns land in the same week (unless the turn crossed a Monday).
        week_counter = UsageCounter.objects.get(
            user=self.user, window_type="week", window_start=local_week_start(today)
        )
        self.assertEqual(week_counter.chats, 2)

    def test_unknown_model_is_recorded_but_flagged(self):
        record = UsageService.record_turn(
            user=self.user, provider="ghost", model_id="ghost-1",
            prompt_tokens=10, completion_tokens=1, now=timezone.now(),
        )
        self.assertIsNotNone(record)
        self.assertIsNone(record.offering)
        self.assertEqual(record.cost_usd_micros, 0)


class QuotaDecisionTests(BillingTestCase):

    def _use_chats(self, count, now=None):
        now = now or timezone.now()
        for _ in range(count):
            UsageService.record_turn(
                user=self.user, provider="gemini", model_id="gemini-3.8-flash",
                prompt_tokens=10, completion_tokens=5, now=now,
            )

    def test_under_the_cap_is_allowed(self):
        self._use_chats(5)
        decision = QuotaService.check(self.user, kind="chat")
        self.assertEqual(decision.state, "allowed")
        self.assertTrue(decision.allowed)
        self.assertEqual(decision.remaining["day"], 15)

    def test_over_the_daily_cap_queues_but_never_blocks(self):
        self._use_chats(20)
        decision = QuotaService.check(self.user, kind="chat")
        self.assertEqual(decision.state, "queued")
        self.assertFalse(decision.allowed)
        self.assertEqual(decision.remaining["day"], 0)
        # The weekly allowance is still there — the day cap is the smoother.
        self.assertEqual(decision.remaining["week"], 40)
        self.assertIsNotNone(decision.resets_at)

    def test_premium_turns_on_the_free_tier_require_an_upgrade(self):
        decision = QuotaService.check(self.user, kind="premium_turn")
        self.assertEqual(decision.state, "upgrade_required")
        self.assertEqual(decision.reason, "premium_not_in_tier")

    def test_subscription_raises_the_entitlements(self):
        tier = Tier.objects.get(code="pro_monthly")
        Subscription.objects.create(
            user=self.user, tier=tier, status=Subscription.Status.ACTIVE,
            starts_at=timezone.now() - timedelta(days=1),
            ends_at=timezone.now() + timedelta(days=29),
        )
        self.assertEqual(TierService.resolve_tier(self.user).code, "pro_monthly")
        decision = QuotaService.check(self.user, kind="chat")
        self.assertEqual(decision.remaining["day"], 200)
        # The frontier model is now within the tier's allowance.
        premium = QuotaService.check(self.user, kind="premium_turn")
        self.assertEqual(premium.state, "allowed")
        self.assertEqual(premium.remaining["day"], 3)

    def test_expired_subscription_falls_back_to_the_free_tier(self):
        tier = Tier.objects.get(code="pro_monthly")
        Subscription.objects.create(
            user=self.user, tier=tier, status=Subscription.Status.ACTIVE,
            starts_at=timezone.now() - timedelta(days=40),
            ends_at=timezone.now() - timedelta(days=10),
        )
        self.assertEqual(TierService.resolve_tier(self.user).code, "ember")
        self.assertEqual(QuotaService.check(self.user, kind="chat").remaining["day"], 20)

    def test_anonymous_user_gets_the_default_tier(self):
        from django.contrib.auth.models import AnonymousUser

        self.assertEqual(TierService.resolve_tier(AnonymousUser()).code, "ember")


class UsageSnapshotTests(BillingTestCase):

    def test_snapshot_reports_the_numbers_the_ui_renders(self):
        UsageService.record_turn(
            user=self.user, provider="gemini", model_id="gemini-3.8-flash",
            prompt_tokens=900, completion_tokens=120, now=timezone.now(),
        )
        snapshot = QuotaService.snapshot(self.user)
        self.assertEqual(snapshot["tier"]["code"], "ember")
        self.assertTrue(snapshot["tier"]["is_free"])
        self.assertEqual(snapshot["windows"]["day"]["chats"]["used"], 1)
        self.assertEqual(snapshot["windows"]["day"]["chats"]["limit"], 20)
        self.assertEqual(snapshot["windows"]["day"]["chats"]["remaining"], 19)
        self.assertEqual(snapshot["windows"]["day"]["tokens"]["prompt"], 900)
        self.assertEqual(snapshot["windows"]["day"]["tokens"]["completion"], 120)
        self.assertIsNotNone(snapshot["resets_at"]["day"])
        self.assertIsNotNone(snapshot["resets_at"]["week"])
        # 14 days of history for the sparkline.
        self.assertEqual(len(snapshot["series"]), 14)
        self.assertEqual(snapshot["series"][-1]["chats"], 1)
        # The entitlement list drives the "what your tier includes" panel.
        keys = {item["key"] for item in snapshot["entitlements"]}
        self.assertIn("chats", keys)
        self.assertIn("images", keys)


class SubscriptionOverlayTests(TestCase):
    """The full-screen upgrade sheet: tier cards + the M-Pesa payment step."""

    def test_overlay_renders_the_tier_cards_and_the_payment_step(self):
        from django.template.loader import render_to_string

        catalogue = [
            {
                "code": "ember",
                "name": "Ember",
                "rank": 0,
                "price_kes": 0.0,
                "duration_days": 0,
                "description": "Free tier",
                "includes": [{"label": "Chat turns", "value": "20 per day"}],
                "is_current": True,
            },
            {
                "code": "pro_monthly",
                "name": "Pro Monthly",
                "rank": 2,
                "price_kes": 300.0,
                "duration_days": 30,
                "description": "A month of Spark",
                "includes": [{"label": "Premium model turns", "value": "3 per day"}],
                "is_current": False,
            },
        ]
        html = render_to_string(
            "pwanimate/settings/partials/subscription_overlay.html",
            {"tier_catalogue": catalogue},
        )
        # Both tiers are offered, with their real prices and entitlements.
        self.assertIn("Ember", html)
        self.assertIn("Pro Monthly", html)
        self.assertIn("KSh 300", html)
        self.assertIn("Premium model turns", html)
        # The current plan is marked and not re-buyable.
        self.assertIn("Your plan", html)
        self.assertIn("Current plan", html)
        # The payment step asks for a phone number (M-Pesa), not a card.
        self.assertIn("pwanimatePaymentPhone", html)
        self.assertNotIn("card number", html.lower())
        # Accessibility: it is a labelled modal dialog.
        self.assertIn('role="dialog"', html)
        self.assertIn('aria-modal="true"', html)


class BillingApiTests(BillingTestCase):

    def setUp(self):
        from rest_framework.test import APIClient

        self.client = APIClient()
        self.client.force_authenticate(user=self.user)

    def test_quota_endpoint_is_compact_and_authenticated(self):
        UsageService.record_turn(
            user=self.user, provider="gemini", model_id="gemini-3.8-flash",
            prompt_tokens=100, completion_tokens=20, now=timezone.now(),
        )
        resp = self.client.get("/api/pwanimate/billing/quota/")
        self.assertEqual(resp.status_code, 200)
        payload = resp.json()["quota"]
        self.assertEqual(payload["chats"]["used"], 1)
        self.assertEqual(payload["chats"]["limit"], 20)
        self.assertEqual(payload["chats"]["remaining"], 19)
        self.assertEqual(payload["tokens"]["total"], 120)
        self.assertEqual(payload["state"], "allowed")
        self.assertEqual(payload["tier"]["code"], "ember")

    def test_usage_endpoint_returns_the_full_snapshot(self):
        resp = self.client.get("/api/pwanimate/billing/usage/")
        self.assertEqual(resp.status_code, 200)
        usage = resp.json()["usage"]
        self.assertIn("windows", usage)
        self.assertIn("series", usage)
        self.assertIn("entitlements", usage)
        self.assertEqual(usage["tier"]["code"], "ember")

    def test_tiers_endpoint_lists_the_catalogue(self):
        resp = self.client.get("/api/pwanimate/billing/tiers/")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual([t["code"] for t in body["tiers"]],
                         ["ember", "day_pass", "pro_monthly", "teams"])
        self.assertEqual(body["current_tier"], "ember")

    def test_endpoints_require_authentication(self):
        from rest_framework.test import APIClient

        anon = APIClient()
        for url in ("/api/pwanimate/billing/quota/", "/api/pwanimate/billing/usage/",
                    "/api/pwanimate/billing/tiers/"):
            self.assertIn(anon.get(url).status_code, (401, 403), url)


class OrchestratorUsageHookTests(BillingTestCase):
    """The orchestrator records what it actually spent."""

    def test_record_usage_hook_writes_the_turn(self):
        from pwanimate.orchestrator import PwanimateOrchestrator

        request = mock.Mock()
        request.user = self.user
        request.attachments = []
        response = mock.Mock()
        response.provider = "gemini"
        response.model = "gemini-3.8-flash"
        response.prompt_tokens = 800
        response.completion_tokens = 200
        response.total_tokens = 1000

        PwanimateOrchestrator._record_usage(request, response)

        record = UsageRecord.objects.get(user=self.user)
        self.assertEqual(record.prompt_tokens, 800)
        self.assertEqual(record.completion_tokens, 200)
        self.assertEqual(record.total_tokens, 1000)
        self.assertEqual(record.kind, "chat")

    def test_record_usage_never_breaks_a_turn(self):
        from pwanimate.orchestrator import PwanimateOrchestrator

        request = mock.Mock()
        request.user = self.user
        request.attachments = []
        response = mock.Mock()
        response.provider = "gemini"
        response.model = "gemini-3.8-flash"
        response.prompt_tokens = None
        response.completion_tokens = None
        response.total_tokens = None

        # Must not raise even with a broken response object.
        PwanimateOrchestrator._record_usage(request, response)
