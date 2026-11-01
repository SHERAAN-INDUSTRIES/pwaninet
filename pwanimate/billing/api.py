"""
Billing & usage API.

- GET /api/pwanimate/billing/quota/     compact payload for the composer chip
  (the quota spinner) — cheap enough to poll.
- GET /api/pwanimate/billing/usage/     the full snapshot for the hover modal
  and the settings dashboard.
- GET /api/pwanimate/billing/tiers/     the tier catalogue + the premium
  capability the caller may use.
- GET /api/pwanimate/billing/subscription/  the caller's subscription and
  recent payments.
- POST /api/pwanimate/billing/mpesa/stk/          start an M-Pesa payment.
- POST /api/pwanimate/billing/mpesa/callback/     the Daraja webhook.
- POST /api/pwanimate/billing/mpesa/demo-complete/  simulate that webhook
  (demo mode only — it exercises the real verification path).

Both reads and writes are backed by the same counters the quota check
enforces, so the number the student sees is the number we act on.
"""

import logging

from django.conf import settings
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import csrf_exempt
from rest_framework import permissions, status
from rest_framework.response import Response
from rest_framework.views import APIView

from .daraja import DarajaError
from .models import Payment
from .payments import create_payment, handle_callback
from .services import QuotaService, TierService

logger = logging.getLogger(__name__)


class PwanimateBillingQuotaView(APIView):
    """Compact: what the composer chip needs on every render."""

    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        snapshot = QuotaService.snapshot(request.user, days=1)
        windows = snapshot.get("windows", {})
        day = windows.get("day", {})
        chats = day.get("chats", {}) or {}
        images = day.get("images", {}) or {}
        tokens = day.get("tokens", {}) or {}
        return Response(
            {
                "quota": {
                    "tier": snapshot.get("tier"),
                    "state": snapshot.get("state"),
                    "chats": {
                        "used": chats.get("used", 0),
                        "limit": chats.get("limit"),
                        "remaining": chats.get("remaining"),
                    },
                    "images": {
                        "used": images.get("used", 0),
                        "limit": images.get("limit"),
                        "remaining": images.get("remaining"),
                    },
                    "tokens": {
                        "prompt": tokens.get("prompt", 0),
                        "completion": tokens.get("completion", 0),
                        "total": tokens.get("total", 0),
                    },
                    "resets_at": snapshot.get("resets_at", {}).get("day"),
                }
            },
            status=status.HTTP_200_OK,
        )


class PwanimateBillingUsageView(APIView):
    """Full: the hover modal and the settings → Usage dashboard."""

    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        snapshot = QuotaService.snapshot(request.user)
        return Response({"usage": snapshot}, status=status.HTTP_200_OK)


class PwanimateBillingTiersView(APIView):
    """The tier catalogue + the caller's entitlements (the UI renders from this)."""

    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        from .models import CostClass, ModelOffering, Tier

        tiers = []
        for tier in Tier.objects.filter(is_active=True).order_by("rank"):
            tiers.append(
                {
                    "code": tier.code,
                    "name": tier.name,
                    "rank": tier.rank,
                    "price_kes": float(tier.price_kes),
                    "duration_days": tier.duration_days,
                    "description": tier.description,
                    "entitlements": tier.entitlements or {},
                }
            )
        current = TierService.resolve_tier(request.user)
        current_rank = current.rank if current else 0

        # The premium capability (frontier/paid class) the caller's tier may
        # use, with the allowance it has left today. The UI renders a chip
        # from this — it never hardcodes a model name.
        premium = []
        for offering in ModelOffering.objects.filter(
            is_active=True, cost_class__in=[CostClass.FRONTIER, CostClass.PAID_FLASH]
        ).select_related("min_tier"):
            allowed = offering.min_tier is None or offering.min_tier.rank <= current_rank
            decision = QuotaService.check(request.user, kind="premium_turn")
            premium.append(
                {
                    "key": offering.key,
                    "label": offering.display_name or offering.model_id,
                    "capability": "frontier" if offering.cost_class == CostClass.FRONTIER else "premium",
                    "allowed": allowed,
                    "required_tier": offering.min_tier.code if offering.min_tier else None,
                    "remaining_today": decision.remaining.get("day"),
                    "state": decision.state,
                }
            )
        return Response(
            {
                "tiers": tiers,
                "current_tier": current.code if current else None,
                "premium": premium,
            },
            status=status.HTTP_200_OK,
        )


class PwanimateBillingSubscriptionView(APIView):
    """The caller's subscription and recent payments."""

    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        subscription = TierService.active_subscription(request.user)
        payments = Payment.objects.filter(user=request.user).select_related("tier")[:10]
        return Response(
            {
                "subscription": {
                    "tier": subscription.tier.code if subscription else None,
                    "tier_name": subscription.tier.name if subscription else None,
                    "status": subscription.status if subscription else None,
                    "starts_at": subscription.starts_at.isoformat() if subscription else None,
                    "ends_at": subscription.ends_at.isoformat() if subscription else None,
                    "is_demo": bool(subscription and subscription.source == "daraja-demo"),
                }
                if subscription
                else None,
                "payments": [
                    {
                        "reference": payment.reference,
                        "tier": payment.tier.code,
                        "amount_kes": float(payment.amount_kes),
                        "status": payment.status,
                        "is_demo": payment.is_demo,
                        "created_at": payment.created_at.isoformat(),
                        "result_desc": payment.result_desc,
                    }
                    for payment in payments
                ],
            },
            status=status.HTTP_200_OK,
        )


class PwanimateBillingStkView(APIView):
    """Start an M-Pesa Daraja STK push for a tier."""

    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        data = request.data or {}
        tier_code = data.get("tier_code") or data.get("tier")
        phone = data.get("phone") or data.get("msisdn")
        if not tier_code:
            return Response(
                {"error": "tier_code is required."}, status=status.HTTP_400_BAD_REQUEST
            )
        if not phone:
            return Response(
                {"error": "phone is required."}, status=status.HTTP_400_BAD_REQUEST
            )
        try:
            payment, result = create_payment(request.user, tier_code, phone)
        except DarajaError as exc:
            return Response({"error": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(
            {
                "payment": {
                    "reference": payment.reference,
                    "amount_kes": float(payment.amount_kes),
                    "tier": payment.tier.code,
                    "status": payment.status,
                    "demo": payment.is_demo,
                },
                "checkout_request_id": result.get("checkout_request_id"),
                "demo": bool(result.get("demo")),
                "message": (
                    "Check your phone for the M-Pesa prompt and enter your PIN."
                    if not result.get("demo")
                    else "Demo mode: no money moves. Complete the simulated payment to activate the tier."
                ),
            },
            status=status.HTTP_202_ACCEPTED,
        )


@method_decorator(csrf_exempt, name="dispatch")
class PwanimateBillingDarajaCallbackView(APIView):
    """
    The Daraja webhook.

    Public (Daraja calls it with no credentials) but verified: the account
    reference must match one of our pending payments and the amount must
    match our amount — the callback itself is not signed.
    """

    permission_classes = [permissions.AllowAny]
    authentication_classes = []

    def post(self, request):
        payload = request.data if isinstance(request.data, dict) else {}
        payment, activated = handle_callback(payload)
        if payment is None:
            # Unknown reference: acknowledge so Daraja stops retrying, but act
            # on nothing.
            return Response({"ResultCode": 0, "ResultDesc": "Ignored"}, status=status.HTTP_200_OK)
        logger.info(
            "[BILLING] Daraja callback for %s -> %s (activated=%s)",
            payment.reference, payment.status, activated,
        )
        return Response(
            {"ResultCode": 0, "ResultDesc": "Accepted" if activated else "Recorded"},
            status=status.HTTP_200_OK,
        )


class PwanimateBillingDemoCompleteView(APIView):
    """
    Simulate the Daraja callback for a demo payment.

    Demo mode only, and it runs the REAL verification path (reference +
    amount), so the flow that prod will use is the flow we test.
    """

    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        if not getattr(settings, "DARAJA_DEMO_ENABLED", False):
            return Response(
                {"error": "Demo payments are disabled."}, status=status.HTTP_400_BAD_REQUEST
            )
        data = request.data or {}
        reference = data.get("reference")
        payment = Payment.objects.filter(
            user=request.user, reference=reference, is_demo=True
        ).first()
        if payment is None:
            return Response(
                {"error": "Unknown demo payment."}, status=status.HTTP_404_NOT_FOUND
            )
        payload = {
            "Body": {
                "stk_callback": {
                    "ResultCode": 0,
                    "ResultDesc": "Accepted (demo)",
                    "CheckoutRequestID": payment.checkout_request_id,
                    "CallbackMetadata": {
                        "Item": [
                            {"Name": "Amount", "Value": float(payment.amount_kes)},
                            {"Name": "AccountReference", "Value": payment.reference},
                            {"Name": "TransactionID", "Value": f"DEMO-{payment.reference[-8:]}"},
                        ]
                    },
                }
            }
        }
        payment, activated = handle_callback(payload)
        return Response(
            {
                "activated": activated,
                "status": payment.status if payment else None,
                "tier": payment.tier.code if payment else None,
            },
            status=status.HTTP_200_OK,
        )
