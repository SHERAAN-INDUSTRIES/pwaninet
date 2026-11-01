"""
Payment flow: create a payment, verify the callback, activate the tier.

Verification matters: Daraja's basic STK-push callback is **not signed**, so
anyone who learns the callback URL could post a fake success. We therefore
accept a callback only when it matches facts only we know:

1. the ``AccountReference`` matches a pending payment of ours, and
2. the ``Amount`` in the callback equals that payment's amount.

Anything else is quarantined, and `DarajaClient.transaction_status()` gives us
the reconciliation path (query the provider itself) for suspicious cases.
"""

import logging
import uuid
from datetime import timedelta
from typing import Any, Dict, Optional, Tuple

from django.db import transaction
from django.utils import timezone

from .daraja import DarajaClient, DarajaError, normalise_msisdn
from .models import Payment, Subscription, Tier

logger = logging.getLogger(__name__)


def _reference() -> str:
    return f"SPARK-{uuid.uuid4().hex[:16].upper()}"


def create_payment(
    user,
    tier_code: str,
    phone: str,
    client: Optional[DarajaClient] = None,
    now=None,
) -> Tuple[Payment, Dict[str, Any]]:
    """
    Start a payment for a tier.

    The amount always comes from the tier — the client never sends an amount.
    Returns (payment, stk_result).
    """
    tier = Tier.objects.filter(code=str(tier_code or "").strip().lower(), is_active=True).first()
    if tier is None:
        raise DarajaError("Unknown tier.")
    if tier.rank <= 0 or float(tier.price_kes) <= 0:
        raise DarajaError("That tier does not need a payment.")
    msisdn = normalise_msisdn(phone)

    client = client or DarajaClient()
    reference = _reference()
    with transaction.atomic():
        payment = Payment.objects.create(
            user=user,
            tier=tier,
            amount_kes=tier.price_kes,
            phone=msisdn,
            reference=reference,
            status=Payment.Status.PENDING,
            is_demo=client.is_demo,
        )
    try:
        result = client.stk_push(
            phone=msisdn,
            amount_kes=float(tier.price_kes),
            reference=reference,
            description=f"{tier.name} ({tier.duration_days} day tier)",
        )
    except DarajaError as exc:
        payment.status = Payment.Status.FAILED
        payment.result_desc = str(exc)[:255]
        payment.save(update_fields=["status", "result_desc", "updated_at"])
        raise

    payment.merchant_request_id = result.get("merchant_request_id", "")[:80]
    payment.checkout_request_id = result.get("checkout_request_id", "")[:80]
    payment.save(update_fields=["merchant_request_id", "checkout_request_id", "updated_at"])
    return payment, result


def _callback_items(callback: Dict[str, Any]) -> Dict[str, Any]:
    """Flatten Daraja's CallbackMetadata.Item list into a dict."""
    items: Dict[str, Any] = {}
    metadata = callback.get("CallbackMetadata") or {}
    entries = metadata.get("Item") if isinstance(metadata, dict) else None
    if isinstance(entries, list):
        for entry in entries:
            if isinstance(entry, dict):
                items[str(entry.get("Name", "")).lower()] = entry.get("Value")
    return items


def parse_callback(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Normalise the Daraja callback (nested or flat) into one dict."""
    callback: Dict[str, Any] = {}
    if isinstance(payload, dict):
        body = payload.get("Body")
        if isinstance(body, dict) and isinstance(body.get("stk_callback"), dict):
            callback = dict(body["stk_callback"])
        elif isinstance(payload.get("stk_callback"), dict):
            callback = dict(payload["stk_callback"])
        else:
            callback = dict(payload)
    items = _callback_items(callback)
    reference = str(
        callback.get("AccountReference")
        or items.get("accountreference")
        or ""
    )
    amount = items.get("amount")
    transaction_id = items.get("transactionid") or callback.get("TransactionID") or ""
    return {
        "result_code": str(callback.get("ResultCode", "")),
        "result_desc": str(callback.get("ResultDesc", ""))[:255],
        "reference": reference,
        "amount": amount,
        "transaction_id": str(transaction_id),
        "checkout_request_id": str(callback.get("CheckoutRequestID", "")),
        "merchant_request_id": str(callback.get("MerchantRequestID", "")),
    }


def activate_subscription(payment: Payment, now=None) -> Subscription:
    """Grant (or extend) the tier the payment bought. Idempotent."""
    now = now or timezone.now()
    tier = payment.tier
    ends_at = now + timedelta(days=tier.duration_days) if tier.duration_days else None

    # select_for_update needs a transaction: two callbacks arriving together
    # must not both create (or extend) a subscription.
    with transaction.atomic():
        subscription = (
            Subscription.objects.select_for_update()
            .filter(user=payment.user, tier=tier, status=Subscription.Status.ACTIVE)
            .order_by("-ends_at")
            .first()
        )
        if subscription and subscription.ends_at and ends_at and subscription.ends_at > ends_at:
            subscription.ends_at = ends_at
            subscription.save(update_fields=["ends_at", "updated_at"])
        elif subscription is None:
            subscription = Subscription.objects.create(
                user=payment.user,
                tier=tier,
                status=Subscription.Status.ACTIVE,
                starts_at=now,
                ends_at=ends_at,
                source="daraja",
                reference=payment.reference,
            )
    return subscription


def handle_callback(payload: Dict[str, Any], now=None) -> Tuple[Optional[Payment], bool]:
    """
    Process a Daraja callback.

    Returns (payment, activated). `payment` is None when the reference is
    unknown (we do not trust or act on it). A callback that fails the amount
    check is recorded but does NOT activate the tier.
    """
    parsed = parse_callback(payload)
    reference = parsed["reference"]
    if not reference:
        logger.warning("[BILLING] Daraja callback without an account reference — ignored.")
        return None, False

    payment = Payment.objects.filter(reference=reference).select_related("tier").first()
    if payment is None:
        logger.warning("[BILLING] Daraja callback for an unknown reference %s — ignored.", reference)
        return None, False

    if payment.status == Payment.Status.PAID:
        return payment, True  # already settled — idempotent

    raw = payload if isinstance(payload, dict) else {}
    payment.raw_callback = raw
    payment.result_code = parsed["result_code"][:20]
    payment.result_desc = parsed["result_desc"]
    if parsed["transaction_id"]:
        payment.transaction_code = parsed["transaction_id"][:80]
    if parsed["checkout_request_id"] and not payment.checkout_request_id:
        payment.checkout_request_id = parsed["checkout_request_id"][:80]

    succeeded = parsed["result_code"] in ("0", "0.0")
    if succeeded:
        # Server-side fact check: the amount in the callback must equal ours.
        amount = parsed["amount"]
        try:
            amount_matches = abs(float(amount) - float(payment.amount_kes)) < 0.01
        except (TypeError, ValueError):
            amount_matches = False
        if not amount_matches:
            logger.error(
                "[BILLING] Daraja callback for %s reported amount %s but the payment is %s — "
                "not activating; reconcile with the provider.",
                reference, amount, payment.amount_kes,
            )
            payment.status = Payment.Status.PENDING
            payment.result_desc = "Amount mismatch — held for reconciliation."
            payment.save(update_fields=["status", "result_desc", "raw_callback", "updated_at"])
            return payment, False
        payment.status = Payment.Status.PAID
        payment.paid_at = now or timezone.now()
        activate_subscription(payment, now)
    else:
        payment.status = Payment.Status.FAILED
    payment.save(
        update_fields=[
            "status", "result_code", "result_desc", "transaction_code", "raw_callback",
            "paid_at", "updated_at",
        ]
    )
    return payment, succeeded
