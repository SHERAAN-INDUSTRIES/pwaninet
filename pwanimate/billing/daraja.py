"""
M-Pesa Daraja client (STK push).

Sandbox-first: with no credentials configured the client runs in DEMO mode —
no network call, a synthetic checkout id, and the simulated callback that the
demo endpoint triggers. Moving to production is only a matter of setting the
live credentials and flipping DARAJA_ENV (the request shape is identical).

Daraja's basic STK-push API (sandbox.safaricom.co.ke / api.safaricom.co.ke):

    POST /mpesa/stkpush/v1/processrequest
    Authorization: Basic base64(consumerKey:consumerSecret)
    {
      "BusinessShortCode": "...", "Password": "base64(shortcode+passkey+timestamp)",
      "Timestamp": "2026-10-20T17:00:00+03:00", "TransactionType": "CustomerPayBillOnline",
      "Amount": 300, "PartyA": "2547XXXXXXXX", "PartyB": "...",
      "PhoneNumber": "2547XXXXXXXX", "CallBackURL": "https://...",
      "AccountReference": "SPARK-...", "TransactionDesc": "Pro Monthly (30 days)"
    }

The result arrives later on the callback URL (which is NOT signed) — see
`pwanimate.billing.payments` for how we verify and activate it.
"""

import base64
import logging
import uuid
from datetime import datetime
from typing import Any, Dict, Optional

import requests
from django.conf import settings
from django.utils import timezone

logger = logging.getLogger(__name__)


class DarajaError(Exception):
    """The payment provider could not be reached or refused the request."""


class DarajaNotConfigured(DarajaError):
    """No credentials are configured (the caller should use demo mode)."""


def _basic_auth(consumer_key: str, consumer_secret: str) -> str:
    raw = f"{consumer_key}:{consumer_secret}".encode("utf-8")
    return base64.b64encode(raw).decode("ascii")


def _password(shortcode: str, passkey: str, timestamp: str) -> str:
    raw = f"{shortcode}{passkey}{timestamp}".encode("utf-8")
    return base64.b64encode(raw).decode("ascii")


def normalise_msisdn(raw: str) -> str:
    """Normalise a Kenyan phone number to 2547XXXXXXXX."""
    if not raw:
        raise DarajaError("A phone number is required.")
    digits = "".join(ch for ch in str(raw) if ch.isdigit())
    if digits.startswith("0") and len(digits) == 10:
        digits = "254" + digits[1:]
    elif digits.startswith("+254"):
        digits = "254" + digits[4:]
    if not (digits.startswith("254") and len(digits) == 12):
        raise DarajaError("Enter a valid Kenyan phone number, e.g. 0712 345 678.")
    return digits


class DarajaClient:
    """Thin wrapper over the Daraja STK-push endpoints."""

    def __init__(self, base_url: Optional[str] = None, consumer_key: Optional[str] = None,
                 consumer_secret: Optional[str] = None, shortcode: Optional[str] = None,
                 passkey: Optional[str] = None, timeout: Optional[int] = None):
        self.base_url = (base_url or settings.DARAJA_BASE_URL).rstrip("/") + "/"
        self.consumer_key = consumer_key if consumer_key is not None else settings.DARAJA_CONSUMER_KEY
        self.consumer_secret = (
            consumer_secret if consumer_secret is not None else settings.DARAJA_CONSUMER_SECRET
        )
        self.shortcode = shortcode if shortcode is not None else settings.DARAJA_SHORTCODE
        self.passkey = passkey if passkey is not None else settings.DARAJA_PASSKEY
        self.timeout = timeout or settings.DARAJA_TIMEOUT

    # ------------------------------------------------------------------ state
    @property
    def is_configured(self) -> bool:
        return bool(
            self.consumer_key and self.consumer_secret and self.shortcode and self.passkey
        )

    @property
    def is_demo(self) -> bool:
        """Demo mode: no live credentials, so nothing is charged."""
        return not self.is_configured

    @property
    def environment(self) -> str:
        return getattr(settings, "DARAJA_ENV", "sandbox")

    # --------------------------------------------------------------- requests
    def stk_push(
        self,
        *,
        phone: str,
        amount_kes: float,
        reference: str,
        description: str,
        callback_url: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Send an STK push.

        Returns {"checkout_request_id", "merchant_request_id", "demo"}.
        Raises DarajaError when the provider refuses or is unreachable.
        """
        if self.is_demo:
            # Demo: no network, no money — the simulated callback activates it.
            return {
                "checkout_request_id": f"DEMO-{uuid.uuid4().hex[:16].upper()}",
                "merchant_request_id": f"DEMO-{uuid.uuid4().hex[:12].upper()}",
                "demo": True,
            }

        timestamp = timezone.localtime(timezone.now()).strftime("%Y-%m-%dT%H:%M:%S%z")
        timestamp = timestamp[:-2] + ":" + timestamp[-2:]  # +0300 -> +03:00
        payload = {
            "BusinessShortCode": self.shortcode,
            "Password": _password(self.shortcode, self.passkey, timestamp),
            "Timestamp": timestamp,
            "TransactionType": "CustomerPayBillOnline",
            "Amount": int(round(float(amount_kes))),
            "PartyA": phone,
            "PartyB": self.shortcode,
            "PhoneNumber": phone,
            "CallBackURL": callback_url or settings.DARAJA_CALLBACK_URL,
            "AccountReference": reference,
            "TransactionDesc": description[: 100],
        }
        url = f"{self.base_url}stkpush/v1/processrequest"
        try:
            response = requests.post(
                url,
                json=payload,
                headers={
                    "Authorization": f"Basic {_basic_auth(self.consumer_key, self.consumer_secret)}",
                    "Content-Type": "application/json",
                },
                timeout=self.timeout,
            )
        except requests.exceptions.RequestException as exc:
            raise DarajaError(f"Could not reach the payment provider: {exc}") from exc

        try:
            body = response.json()
        except ValueError as exc:
            raise DarajaError(
                f"The payment provider returned an unreadable response (HTTP {response.status_code})."
            ) from exc

        if response.status_code != 200:
            message = body.get("errorMessage") or body.get("error_description") or str(body)
            raise DarajaError(f"The payment provider refused the request: {message}")

        if not body.get("CheckoutRequestID"):
            raise DarajaError(
                f"The payment provider did not return a checkout id: "
                f"{body.get('errorMessage') or body}"
            )
        return {
            "checkout_request_id": body.get("CheckoutRequestID", ""),
            "merchant_request_id": body.get("MerchantRequestID", ""),
            "demo": False,
        }

    def transaction_status(self, checkout_request_id: str) -> Dict[str, Any]:
        """
        Query the status of an STK push (the reconciliation path — Daraja's
        callback is unsigned, so we confirm suspicious payments against the
        provider itself).
        """
        if self.is_demo:
            return {"demo": True, "result_code": "0", "result_desc": "Demo payment completed."}
        timestamp = timezone.localtime(timezone.now()).strftime("%Y-%m-%dT%H:%M:%S%z")
        timestamp = timestamp[:-2] + ":" + timestamp[-2:]
        payload = {
            "BusinessShortCode": self.shortcode,
            "Password": _password(self.shortcode, self.passkey, timestamp),
            "Timestamp": timestamp,
            "CheckoutRequestID": checkout_request_id,
        }
        url = f"{self.base_url}stkpushquery/v1/query"
        try:
            response = requests.post(
                url,
                json=payload,
                headers={
                    "Authorization": f"Basic {_basic_auth(self.consumer_key, self.consumer_secret)}",
                    "Content-Type": "application/json",
                },
                timeout=self.timeout,
            )
            return response.json()
        except (requests.exceptions.RequestException, ValueError) as exc:
            raise DarajaError(f"Could not query the payment provider: {exc}") from exc
