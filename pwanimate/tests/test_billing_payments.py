"""
Tests for the M-Pesa Daraja flow (demo mode).

The flow that prod will use is the flow we test: a demo STK push creates a
pending payment, and the simulated callback goes through the REAL
verification path (account reference + amount), so the demo cannot drift
from the real integration.

Covers:
- the amount always comes from the tier, never from the client,
- phone normalisation,
- a successful callback activates the subscription (once),
- an unknown reference is ignored (Daraja's callback is unsigned),
- a mismatched amount is recorded but does NOT activate,
- a failed result does not activate,
- the activation is visible to the quota API (the premium chip unlocks).
"""

from io import StringIO

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from pwanimate.billing.models import Payment, Subscription, Tier
from pwanimate.billing.payments import create_payment, handle_callback

DEMO_SETTINGS = override_settings(
    DARAJA_DEMO_ENABLED=True,
    DARAJA_CONSUMER_KEY="",
    DARAJA_CONSUMER_SECRET="",
    DARAJA_SHORTCODE="",
    DARAJA_PASSKEY="",
    DARAJA_ENV="sandbox",
)


def _callback(reference, amount, result_code=0, result_desc="Accepted"):
    return {
        "Body": {
            "stk_callback": {
                "ResultCode": result_code,
                "ResultDesc": result_desc,
                "CheckoutRequestID": "DEMO-123",
                "CallbackMetadata": {
                    "Item": [
                        {"Name": "Amount", "Value": amount},
                        {"Name": "AccountReference", "Value": reference},
                        {"Name": "TransactionID", "Value": "TX-1"},
                    ]
                },
            }
        }
    }


@DEMO_SETTINGS
class DarajaDemoFlowTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("seed_model_registry", verbosity=0, stdout=StringIO())
        cls.user = get_user_model().objects.create_user(
            username="payer", email="payer@vasto.local", password="testpassword123"
        )

    def setUp(self):
        # The client is built per test: Django deep-copies setUpTestData
        # attributes, which would drop the forced authentication.
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)

    def test_stk_push_creates_a_pending_payment_for_the_tier_price(self):
        resp = self.client.post(
            "/api/pwanimate/billing/mpesa/stk/",
            {"tier_code": "pro_monthly", "phone": "0712345678"},
            format="json",
        )
        self.assertEqual(resp.status_code, 202)
        body = resp.json()
        self.assertTrue(body["demo"])
        self.assertTrue(body["checkout_request_id"].startswith("DEMO-"))

        payment = Payment.objects.get(user=self.user)
        self.assertEqual(payment.status, Payment.Status.PENDING)
        # The price comes from the tier, not from the client.
        self.assertEqual(float(payment.amount_kes), 300.0)
        self.assertEqual(payment.phone, "254712345678")
        self.assertEqual(payment.tier.code, "pro_monthly")

    def test_client_supplied_amount_is_ignored(self):
        self.client.post(
            "/api/pwanimate/billing/mpesa/stk/",
            {"tier_code": "pro_monthly", "phone": "0712345678", "amount": 1},
            format="json",
        )
        self.assertEqual(float(Payment.objects.get(user=self.user).amount_kes), 300.0)

    def test_free_tier_needs_no_payment(self):
        resp = self.client.post(
            "/api/pwanimate/billing/mpesa/stk/",
            {"tier_code": "ember", "phone": "0712345678"},
            format="json",
        )
        self.assertEqual(resp.status_code, 400)

    def test_invalid_phone_is_rejected(self):
        resp = self.client.post(
            "/api/pwanimate/billing/mpesa/stk/",
            {"tier_code": "pro_monthly", "phone": "12345"},
            format="json",
        )
        self.assertEqual(resp.status_code, 400)
        self.assertIn("phone", resp.json()["error"].lower())

    def test_demo_completion_activates_the_subscription(self):
        self.client.post(
            "/api/pwanimate/billing/mpesa/stk/",
            {"tier_code": "pro_monthly", "phone": "0712345678"},
            format="json",
        )
        payment = Payment.objects.get(user=self.user)

        resp = self.client.post(
            "/api/pwanimate/billing/mpesa/demo-complete/",
            {"reference": payment.reference},
            format="json",
        )
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.json()["activated"])

        payment.refresh_from_db()
        self.assertEqual(payment.status, Payment.Status.PAID)

        subscription = Subscription.objects.get(user=self.user)
        self.assertEqual(subscription.tier.code, "pro_monthly")
        self.assertEqual(subscription.status, Subscription.Status.ACTIVE)
        self.assertGreater(subscription.ends_at, timezone.now())

        # The premium capability is now unlocked and the tier shows everywhere.
        tiers = self.client.get("/api/pwanimate/billing/tiers/").json()
        self.assertEqual(tiers["current_tier"], "pro_monthly")
        premium = {item["key"]: item for item in tiers["premium"]}
        self.assertTrue(premium["frontier-pro"]["allowed"])
        self.assertEqual(premium["frontier-pro"]["remaining_today"], 3)

    def test_callback_with_unknown_reference_is_ignored(self):
        payment, activated = handle_callback(_callback("SPARK-NOPE", 300.0))
        self.assertIsNone(payment)
        self.assertFalse(activated)
        self.assertEqual(Subscription.objects.count(), 0)

    def test_callback_with_the_wrong_amount_does_not_activate(self):
        self.client.post(
            "/api/pwanimate/billing/mpesa/stk/",
            {"tier_code": "pro_monthly", "phone": "0712345678"},
            format="json",
        )
        payment = Payment.objects.get(user=self.user)

        # A forged "success" with a different amount must not grant the tier.
        handled, activated = handle_callback(_callback(payment.reference, 1.0))
        self.assertFalse(activated)
        self.assertEqual(Subscription.objects.count(), 0)
        handled.refresh_from_db()
        self.assertNotEqual(handled.status, Payment.Status.PAID)

    def test_callback_with_a_failed_result_does_not_activate(self):
        self.client.post(
            "/api/pwanimate/billing/mpesa/stk/",
            {"tier_code": "pro_monthly", "phone": "0712345678"},
            format="json",
        )
        payment = Payment.objects.get(user=self.user)

        handled, activated = handle_callback(
            _callback(payment.reference, 300.0, result_code=1, result_desc="Cancelled by user")
        )
        self.assertFalse(activated)
        self.assertEqual(handled.status, Payment.Status.FAILED)
        self.assertEqual(Subscription.objects.count(), 0)

    def test_callback_is_idempotent(self):
        self.client.post(
            "/api/pwanimate/billing/mpesa/stk/",
            {"tier_code": "pro_monthly", "phone": "0712345678"},
            format="json",
        )
        payment = Payment.objects.get(user=self.user)
        handle_callback(_callback(payment.reference, 300.0))
        handle_callback(_callback(payment.reference, 300.0))
        self.assertEqual(Subscription.objects.filter(user=self.user).count(), 1)

    def test_the_webhook_is_public_and_the_demo_endpoint_is_not(self):
        from django.test import Client

        anon = Client()
        resp = anon.post(
            "/api/pwanimate/billing/mpesa/callback/",
            _callback("SPARK-UNKNOWN", 300.0),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 200)  # acknowledged, but nothing activated
        self.assertEqual(Subscription.objects.count(), 0)

        # The demo endpoint is for the signed-in student only.
        from rest_framework.test import APIClient as _APIClient

        unauth = _APIClient()
        self.assertIn(
            unauth.post(
                "/api/pwanimate/billing/mpesa/demo-complete/", {"reference": "x"}, format="json"
            ).status_code,
            (401, 403),
        )

    def test_subscription_endpoint_reports_the_purchase(self):
        self.client.post(
            "/api/pwanimate/billing/mpesa/stk/",
            {"tier_code": "day_pass", "phone": "0712345678"},
            format="json",
        )
        payment = Payment.objects.get(user=self.user)
        self.client.post(
            "/api/pwanimate/billing/mpesa/demo-complete/",
            {"reference": payment.reference},
            format="json",
        )
        body = self.client.get("/api/pwanimate/billing/subscription/").json()
        self.assertEqual(body["subscription"]["tier"], "day_pass")
        self.assertEqual(body["subscription"]["status"], "active")
        self.assertEqual(len(body["payments"]), 1)
        self.assertEqual(body["payments"][0]["status"], "paid")
