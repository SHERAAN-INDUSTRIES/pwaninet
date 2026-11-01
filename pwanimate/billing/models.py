"""
Billing & usage domain (docs/billing-tiers-design.md).

Models:
- Tier           the tier catalogue + entitlements (what a tier may do)
- ModelOffering  the model registry: every servable model, its cost class
                 and the lowest tier allowed to reach it (D8)
- Subscription   a user's paid entitlement (or the default/free tier)
- UsageRecord    append-only per-action record (the source of truth)
- UsageCounter   rollups per user per window (day / week / period)

The registry and the counters are the same source of truth: what the UI
shows is exactly what the quota check enforces.
"""

from django.conf import settings
from django.db import models
from django.utils import timezone
from typing import Optional


class CostClass(models.TextChoices):
    """What a model costs us — drives the price of a tier, never the user's cap."""

    FREE_ROUTE = "free_route", "Free route"
    PAID_FLASH = "paid_flash", "Paid (flash class)"
    FRONTIER = "frontier", "Frontier"
    VISION_EXTERNAL = "vision_external", "External vision"


class WindowType(models.TextChoices):
    DAY = "day", "Day"
    WEEK = "week", "Week"
    PERIOD = "period", "Period"


class UsageKind(models.TextChoices):
    CHAT = "chat", "Chat"
    IMAGE = "image", "Image"
    TOOL = "tool", "Tool"


class Tier(models.Model):
    """A tier in the catalogue. `rank` orders them (0 = the free tier)."""

    code = models.CharField(max_length=32, unique=True)
    name = models.CharField(max_length=64)
    rank = models.PositiveIntegerField(unique=True)
    price_kes = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    duration_days = models.PositiveIntegerField(default=0, help_text="0 = no expiry (free tier)")
    entitlements = models.JSONField(
        default=dict,
        blank=True,
        help_text=(
            'Entitlement caps, e.g. {"chats": {"day": 20, "week": 60}, '
            '"images": {"day": 5}, "premium_turns": {"day": 0}}'
        ),
    )
    description = models.TextField(blank=True, default="")
    is_default = models.BooleanField(default=False, help_text="The tier every user starts on")
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["rank"]

    def __str__(self):
        return f"{self.name} ({self.code})"

    def entitlement(self, key: str, window: str) -> Optional[int]:
        """Limit for one entitlement key in one window (None = unlimited)."""
        value = (self.entitlements or {}).get(key, {}).get(window)
        return int(value) if value is not None else None


class ModelOffering(models.Model):
    """One servable model. `min_tier` is the lowest tier allowed to reach it."""

    key = models.CharField(max_length=80, unique=True)
    provider = models.CharField(max_length=32)
    model_id = models.CharField(max_length=160)
    display_name = models.CharField(max_length=160, blank=True, default="")
    cost_class = models.CharField(
        max_length=24, choices=CostClass.choices, default=CostClass.FREE_ROUTE
    )
    min_tier = models.ForeignKey(
        Tier,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="offerings",
        help_text="Lowest tier allowed to use this model. Null = the free tier.",
    )
    input_price_per_m_usd = models.DecimalField(
        max_digits=12, decimal_places=6, default=0,
        help_text="USD per million input tokens (0 for free routes).",
    )
    output_price_per_m_usd = models.DecimalField(
        max_digits=12, decimal_places=6, default=0,
        help_text="USD per million output tokens (0 for free routes).",
    )
    capabilities = models.JSONField(default=dict, blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["provider", "model_id"]
        constraints = [
            models.UniqueConstraint(fields=["provider", "model_id"], name="uniq_offering_model"),
        ]

    def __str__(self):
        return f"{self.provider}:{self.model_id}"

    def cost_usd_micros(self, prompt_tokens: int = 0, completion_tokens: int = 0) -> int:
        """Cost of one turn in USD micro-units (1 USD = 1,000,000 micros)."""
        cost = (
            (prompt_tokens / 1_000_000.0) * float(self.input_price_per_m_usd)
            + (completion_tokens / 1_000_000.0) * float(self.output_price_per_m_usd)
        )
        return int(round(cost * 1_000_000))


class Subscription(models.Model):
    """A user's entitlement. Free users have none — they fall back to the default tier."""

    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        EXPIRED = "expired", "Expired"
        CANCELLED = "cancelled", "Cancelled"
        PENDING = "pending", "Pending"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="billing_subscriptions"
    )
    tier = models.ForeignKey(Tier, on_delete=models.PROTECT, related_name="subscriptions")
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.ACTIVE)
    starts_at = models.DateTimeField(default=timezone.now)
    ends_at = models.DateTimeField(
        null=True, blank=True, help_text="Null = no expiry. The 'period' usage window runs to this point."
    )
    source = models.CharField(max_length=32, default="manual")
    reference = models.CharField(max_length=120, unique=True, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-starts_at"]
        indexes = [models.Index(fields=["user", "status"])]

    def __str__(self):
        return f"{self.user} -> {self.tier.code} ({self.status})"

    def is_active(self, now=None) -> bool:
        now = now or timezone.now()
        if self.status != self.Status.ACTIVE:
            return False
        return self.ends_at is None or self.ends_at > now


class Payment(models.Model):
    """One payment attempt (M-Pesa Daraja STK push).

    The amount is always taken from the tier, never from the client.
    `reference` is the account reference we send to Daraja and the handle we
    match the callback on — Daraja's basic callback is not signed, so this
    pairing (plus the amount) is our verification.
    """

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        PAID = "paid", "Paid"
        FAILED = "failed", "Failed"
        EXPIRED = "expired", "Expired"
        CANCELLED = "cancelled", "Cancelled"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="billing_payments"
    )
    tier = models.ForeignKey(Tier, on_delete=models.PROTECT, related_name="payments")
    provider = models.CharField(max_length=32, default="daraja")
    amount_kes = models.DecimalField(max_digits=10, decimal_places=2)
    phone = models.CharField(max_length=20, help_text="Normalised MSISDN, e.g. 254712345678")
    reference = models.CharField(max_length=120, unique=True)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.PENDING)
    merchant_request_id = models.CharField(max_length=80, blank=True, default="")
    checkout_request_id = models.CharField(max_length=80, blank=True, default="")
    transaction_code = models.CharField(max_length=80, blank=True, default="")
    result_code = models.CharField(max_length=20, blank=True, default="")
    result_desc = models.CharField(max_length=255, blank=True, default="")
    is_demo = models.BooleanField(default=False, help_text="Simulated payment (no money moved).")
    raw_callback = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    paid_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["user", "-created_at"])]

    def __str__(self):
        return f"{self.user} {self.tier.code} KSh {self.amount_kes} ({self.status})"


class UsageRecord(models.Model):
    """One metered action — the append-only source of truth."""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="usage_records"
    )
    created_at = models.DateTimeField(default=timezone.now, db_index=True)
    kind = models.CharField(max_length=16, choices=UsageKind.choices, default=UsageKind.CHAT)
    offering = models.ForeignKey(
        ModelOffering, null=True, blank=True, on_delete=models.SET_NULL, related_name="records"
    )
    # Denormalised snapshot: history must survive registry edits.
    provider = models.CharField(max_length=32, blank=True, default="")
    model_id = models.CharField(max_length=160, blank=True, default="")
    cost_class = models.CharField(max_length=24, choices=CostClass.choices, default=CostClass.FREE_ROUTE)
    prompt_tokens = models.IntegerField(default=0)
    completion_tokens = models.IntegerField(default=0)
    total_tokens = models.IntegerField(default=0)
    images = models.IntegerField(default=0)
    cost_usd_micros = models.BigIntegerField(default=0)
    source = models.CharField(max_length=32, default="chat")

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["user", "-created_at"]),
            models.Index(fields=["user", "kind", "-created_at"]),
        ]

    def __str__(self):
        return f"{self.user} {self.kind} {self.provider}:{self.model_id} @ {self.created_at:%Y-%m-%d %H:%M}"


class UsageCounter(models.Model):
    """Rollup per user per window — what the UI reads and the quota check enforces."""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="usage_counters"
    )
    window_type = models.CharField(max_length=16, choices=WindowType.choices)
    window_start = models.DateTimeField()
    chats = models.IntegerField(default=0)
    images = models.IntegerField(default=0)
    premium_turns = models.IntegerField(default=0)
    prompt_tokens = models.BigIntegerField(default=0)
    completion_tokens = models.BigIntegerField(default=0)
    cost_usd_micros = models.BigIntegerField(default=0)

    class Meta:
        ordering = ["window_start"]
        constraints = [
            models.UniqueConstraint(
                fields=["user", "window_type", "window_start"], name="uniq_usage_window"
            ),
        ]
        indexes = [models.Index(fields=["user", "window_type", "-window_start"])]

    def __str__(self):
        return f"{self.user} {self.window_type} {self.window_start:%Y-%m-%d}"
