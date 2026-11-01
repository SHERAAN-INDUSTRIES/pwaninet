"""
Billing services: tier resolution, usage accounting, quota decisions.

The single source of truth: `QuotaService.snapshot()` (what the UI renders)
and `QuotaService.check()` (what the request path enforces) read the same
counters, so a student can never see a number we do not enforce.
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Dict, Iterable, List, Optional, Tuple

from django.db import transaction
from django.db.models import F
from django.utils import timezone

from .models import (
    CostClass,
    ModelOffering,
    Subscription,
    Tier,
    UsageCounter,
    UsageKind,
    UsageRecord,
    WindowType,
)

logger = logging.getLogger(__name__)

# Entitlement key per metered action.
PREMIUM_ENTITLEMENT = "premium_turns"

KIND_TO_ENTITLEMENT = {
    UsageKind.CHAT: "chats",
    UsageKind.IMAGE: "images",
    UsageKind.TOOL: "chats",
    "premium_turn": PREMIUM_ENTITLEMENT,
}

STATE_ALLOWED = "allowed"
STATE_QUEUED = "queued"
STATE_UPGRADE_REQUIRED = "upgrade_required"


def _aware(now: Optional[datetime] = None) -> datetime:
    if now is None:
        return timezone.now()
    if timezone.is_naive(now):
        return timezone.make_aware(now, timezone.get_current_timezone())
    return now


def local_day_start(now: datetime) -> datetime:
    """Midnight of the user's local day (Africa/Nairobi) — the daily window."""
    return timezone.localtime(now).replace(hour=0, minute=0, second=0, microsecond=0)


def local_week_start(now: datetime) -> datetime:
    """Monday 00:00 local — the weekly window."""
    day = local_day_start(now)
    return day - timedelta(days=timezone.localtime(now).weekday())


def next_day_start(now: datetime) -> datetime:
    return local_day_start(now) + timedelta(days=1)


def next_week_start(now: datetime) -> datetime:
    return local_week_start(now) + timedelta(days=7)


# ---------------------------------------------------------------- registry
# The registry is read on every request (once per candidate), so it is cached
# in memory for a short while — the catalogue changes only on deploy.
_OFFERING_INDEX: Dict[str, Any] = {"at": None, "index": {}}
_OFFERING_INDEX_TTL_SECONDS = 60.0


def offering_index(now: Optional[float] = None) -> Dict[Tuple[str, str], ModelOffering]:
    """(provider, model_id) -> offering, cached briefly."""
    import time as _time

    now = now if now is not None else _time.monotonic()
    if _OFFERING_INDEX["index"] and (now - _OFFERING_INDEX["at"]) < _OFFERING_INDEX_TTL_SECONDS:
        return _OFFERING_INDEX["index"]
    index = {}
    for offering in ModelOffering.objects.filter(is_active=True).select_related("min_tier"):
        index[(offering.provider, offering.model_id)] = offering
        index[(offering.provider, offering.model_id.lower())] = offering
    _OFFERING_INDEX["index"] = index
    _OFFERING_INDEX["at"] = now
    return index


def invalidate_offering_index() -> None:
    _OFFERING_INDEX["index"] = {}
    _OFFERING_INDEX["at"] = None


def tier_rank_for_user(user, now: Optional[datetime] = None) -> Optional[int]:
    """The caller's tier rank (0 = free). Convenience wrapper for request paths."""
    return TierService.rank_for(user, now)


def offering_by_key(key: str) -> Optional[ModelOffering]:
    if not key:
        return None
    return ModelOffering.objects.filter(key=key, is_active=True).select_related("min_tier").first()


def is_free_route(provider: str, model_id: str) -> bool:
    """
    Is this candidate recognisably served on a free allowance?

    Only used for candidates that are NOT in the registry (a registered
    offering carries its own cost class and min tier). An unregistered model
    is treated as paid unless it is provably free:
    - OpenRouter: only a ':free' id is free (a plain id would be billed).
    - mock: the friendly-error rung, never a real model.
    - Gemini / Groq: not provably free from the id alone — they must be
      registered, so the registry stays the single source of truth.
    """
    if not provider or not model_id:
        return False
    provider = provider.lower()
    if provider == "mock":
        return True
    if provider == "openrouter":
        return model_id.lower().endswith(":free")
    return False


def offering_allows_tier(offering: Optional[ModelOffering], provider: str, model_id: str, tier_rank: Optional[int]) -> bool:
    """
    May this tier reach this candidate?

    - A registered offering: yes if its min_tier is at or below the caller's rank.
    - An unregistered candidate: allowed only if it is recognisably a free
      route; anything else is treated as paid and withheld from the free tier
      (fail-safe: an unpriced model must not become a free-tier leak).
    """
    if tier_rank is None:
        return True  # no tier context (direct gateway use) -> do not filter
    if offering is not None:
        min_tier = offering.min_tier
        if min_tier is None:
            return True
        return min_tier.rank <= tier_rank
    return is_free_route(provider, model_id)


# ---------------------------------------------------------------- tiers
class TierService:
    """Resolves a user to their tier and their entitlement windows."""

    @staticmethod
    def default_tier() -> Optional[Tier]:
        tier = Tier.objects.filter(is_default=True, is_active=True).order_by("rank").first()
        if tier:
            return tier
        return Tier.objects.filter(is_active=True).order_by("rank").first()

    @classmethod
    def rank_for(cls, user, now: Optional[datetime] = None) -> Optional[int]:
        """The caller's tier rank (0 = the free tier)."""
        tier = cls.resolve_tier(user, now)
        return tier.rank if tier else None

    @staticmethod
    def active_subscription(user, now: Optional[datetime] = None) -> Optional[Subscription]:
        now = _aware(now)
        return (
            Subscription.objects.filter(user=user, status=Subscription.Status.ACTIVE)
            .select_related("tier")
            .order_by("-ends_at", "-starts_at")
            .first()
        )

    @classmethod
    def resolve_tier(cls, user, now: Optional[datetime] = None) -> Optional[Tier]:
        """The user's effective tier: their active subscription, else the default."""
        now = _aware(now)
        if user is None or not getattr(user, "is_authenticated", False):
            return cls.default_tier()
        subscription = cls.active_subscription(user, now)
        if subscription and subscription.is_active(now):
            return subscription.tier
        return cls.default_tier()

    @classmethod
    def resolve_period(cls, user, now: Optional[datetime] = None) -> Tuple[Optional[datetime], Optional[datetime]]:
        """(period_start, period_end) of the user's entitlement window."""
        now = _aware(now)
        if user is None or not getattr(user, "is_authenticated", False):
            return None, None
        subscription = cls.active_subscription(user, now)
        if subscription and subscription.is_active(now):
            return subscription.starts_at, subscription.ends_at
        return None, None


# ---------------------------------------------------------------- accounting
class UsageService:
    """Records metered actions and keeps the window rollups in step."""

    @staticmethod
    def offering_for(provider: str, model_id: str) -> Optional[ModelOffering]:
        if not provider or not model_id:
            return None
        return ModelOffering.objects.filter(
            provider=provider, model_id=model_id, is_active=True
        ).first()

    @classmethod
    def record_turn(
        cls,
        user,
        provider: str = "",
        model_id: str = "",
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        total_tokens: int = 0,
        images: int = 0,
        kind: str = UsageKind.CHAT,
        source: str = "chat",
        chats: int = 1,
        now: Optional[datetime] = None,
    ) -> Optional[UsageRecord]:
        """
        Record one metered turn and roll it into every window counter.

        ``chats`` is 1 for a conversation turn and 0 for work that is metered
        against a different entitlement -- an image analysis spends an image,
        not a chat. It was hardcoded to 1 before, which meant every recorded
        row also spent a chat whether or not it was one.
        """
        if user is None or not getattr(user, "is_authenticated", False):
            return None
        now = _aware(now)
        offering = cls.offering_for(provider, model_id)
        if offering is None:
            logger.warning(
                "[BILLING] No model offering registered for %s:%s — usage recorded with "
                "zero cost. Run `manage.py check_model_registry`.",
                provider, model_id,
            )
            cost_class = CostClass.FREE_ROUTE
            cost_micros = 0
        else:
            cost_class = offering.cost_class
            cost_micros = offering.cost_usd_micros(prompt_tokens, completion_tokens)

        total_tokens = int(total_tokens or (prompt_tokens or 0) + (completion_tokens or 0))
        premium = 1 if cost_class in (CostClass.FRONTIER, CostClass.PAID_FLASH) else 0

        with transaction.atomic():
            record = UsageRecord.objects.create(
                user=user,
                created_at=now,
                kind=kind,
                offering=offering,
                provider=provider or "",
                model_id=model_id or "",
                cost_class=cost_class,
                prompt_tokens=int(prompt_tokens or 0),
                completion_tokens=int(completion_tokens or 0),
                total_tokens=total_tokens,
                images=int(images or 0),
                cost_usd_micros=cost_micros,
                source=source,
            )
            for window_type, window_start in cls._windows(user, now).items():
                cls._bump(
                    user=user,
                    window_type=window_type,
                    window_start=window_start,
                    chats=int(chats),
                    images=int(images or 0),
                    premium_turns=premium,
                    prompt_tokens=int(prompt_tokens or 0),
                    completion_tokens=int(completion_tokens or 0),
                    cost_usd_micros=cost_micros,
                )
        return record

    @classmethod
    def record_image_analysis(
        cls,
        user,
        provider: str = "",
        model_id: str = "",
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        source: str = "vision_analysis",
        now: Optional[datetime] = None,
    ) -> Optional[UsageRecord]:
        """
        Record one image analysis.

        An analysis spends an *image* from the student's allowance, not a
        chat: the analysis happens at upload, on its own, and must not eat
        into the conversation allowance.
        """
        return cls.record_turn(
            user=user,
            provider=provider,
            model_id=model_id,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            images=1,
            kind=UsageKind.IMAGE,
            source=source,
            chats=0,
            now=now,
        )

    @staticmethod
    def _windows(user, now: datetime) -> Dict[str, datetime]:
        windows = {
            WindowType.DAY: local_day_start(now),
            WindowType.WEEK: local_week_start(now),
        }
        period_start, _period_end = TierService.resolve_period(user, now)
        if period_start is not None:
            windows[WindowType.PERIOD] = period_start
        return windows

    @staticmethod
    def _bump(
        user,
        window_type: str,
        window_start: datetime,
        chats: int = 0,
        images: int = 0,
        premium_turns: int = 0,
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        cost_usd_micros: int = 0,
    ) -> None:
        counter, _ = UsageCounter.objects.get_or_create(
            user=user,
            window_type=window_type,
            window_start=window_start,
        )
        UsageCounter.objects.filter(pk=counter.pk).update(
            chats=F("chats") + chats,
            images=F("images") + images,
            premium_turns=F("premium_turns") + premium_turns,
            prompt_tokens=F("prompt_tokens") + prompt_tokens,
            completion_tokens=F("completion_tokens") + completion_tokens,
            cost_usd_micros=F("cost_usd_micros") + cost_usd_micros,
        )

    @classmethod
    def counter(cls, user, window_type: str, window_start: datetime) -> UsageCounter:
        return (
            UsageCounter.objects.filter(
                user=user, window_type=window_type, window_start=window_start
            ).first()
            or UsageCounter(user=user, window_type=window_type, window_start=window_start)
        )


# ---------------------------------------------------------------- quota
@dataclass
class QuotaDecision:
    """The outcome of a quota check — and the numbers the UI renders."""

    state: str = STATE_ALLOWED
    reason: str = ""
    kind: str = UsageKind.CHAT
    entitlement_key: str = "chats"
    used: Dict[str, int] = field(default_factory=dict)
    limits: Dict[str, Optional[int]] = field(default_factory=dict)
    remaining: Dict[str, Optional[int]] = field(default_factory=dict)
    resets_at: Optional[datetime] = None

    @property
    def allowed(self) -> bool:
        return self.state == STATE_ALLOWED

    def as_dict(self) -> Dict[str, Any]:
        return {
            "state": self.state,
            "reason": self.reason,
            "kind": self.kind,
            "entitlement": self.entitlement_key,
            "used": dict(self.used),
            "limits": dict(self.limits),
            "remaining": dict(self.remaining),
            "resets_at": self.resets_at.isoformat() if self.resets_at else None,
        }


class QuotaService:
    """Reads the counters and answers: allowed, queued, or upgrade required."""

    @classmethod
    def check(
        cls,
        user,
        kind: str = UsageKind.CHAT,
        now: Optional[datetime] = None,
        offering: Optional[ModelOffering] = None,
    ) -> QuotaDecision:
        now = _aware(now)
        tier = TierService.resolve_tier(user, now)
        entitlement_key = KIND_TO_ENTITLEMENT.get(kind, "chats")
        decision = QuotaDecision(kind=kind, entitlement_key=entitlement_key)

        if tier is None:
            # No catalogue yet → nothing to enforce (never a hard block).
            decision.state = STATE_ALLOWED
            decision.reason = "no_tier_catalogue"
            return decision

        windows = UsageService._windows(user, now)
        counter_field = {
            "chats": "chats",
            "images": "images",
            PREMIUM_ENTITLEMENT: "premium_turns",
        }.get(entitlement_key, "chats")
        for window_type, window_start in windows.items():
            limit = tier.entitlement(entitlement_key, window_type)
            if limit is None:
                continue
            counter = UsageService.counter(user, window_type, window_start)
            used = getattr(counter, counter_field, 0)
            decision.used[window_type] = used
            decision.limits[window_type] = limit
            decision.remaining[window_type] = max(0, limit - used)
            if used >= limit and decision.state == STATE_ALLOWED:
                if entitlement_key == PREMIUM_ENTITLEMENT and limit == 0:
                    decision.state = STATE_UPGRADE_REQUIRED
                    decision.reason = "premium_not_in_tier"
                else:
                    decision.state = STATE_QUEUED
                    decision.reason = f"{entitlement_key}_{window_type}_exhausted"
                decision.resets_at = (
                    next_day_start(now) if window_type == WindowType.DAY
                    else next_week_start(now) if window_type == WindowType.WEEK
                    else None
                )
        return decision

    @classmethod
    def check_turn(
        cls,
        user,
        image_count: int = 0,
        now: Optional[datetime] = None,
    ) -> Optional[QuotaDecision]:
        """
        The gate for an incoming turn.

        Returns the blocking :class:`QuotaDecision` when the turn may not
        proceed, or ``None`` when it may. This is the check the request path
        must call -- ``check()`` on its own only answers a question, it never
        stops a request.

        The chat allowance is checked first because every turn spends one.
        The image allowance is only consulted when the turn actually carries
        images, so a text-only turn is never blocked by the image cap.
        """
        if user is None or not getattr(user, "is_authenticated", False):
            return None

        chat = cls.check(user, kind=UsageKind.CHAT, now=now)
        if not chat.allowed:
            return chat

        if image_count > 0:
            image = cls.check(user, kind=UsageKind.IMAGE, now=now)
            if not image.allowed:
                return image

        return None

    @classmethod
    def check_premium_turn(cls, user, offering: Optional[ModelOffering], now=None) -> QuotaDecision:
        """Premium/frontier allowance check for a specific offering."""
        now = _aware(now)
        if offering is None or offering.cost_class not in (CostClass.FRONTIER, CostClass.PAID_FLASH):
            return QuotaDecision(state=STATE_ALLOWED, reason="not_premium")
        decision = cls.check(user, kind="premium_turn", now=now)
        decision.entitlement_key = PREMIUM_ENTITLEMENT
        return decision

    @classmethod
    def snapshot(cls, user, now: Optional[datetime] = None, days: int = 14) -> Dict[str, Any]:
        """
        The full picture for the composer chip, the hover modal and the
        settings dashboard. Every number here comes from the counters the
        quota check enforces.
        """
        now = _aware(now)
        tier = TierService.resolve_tier(user, now)
        period_start, period_end = TierService.resolve_period(user, now)
        windows = UsageService._windows(user, now)

        window_payload: Dict[str, Any] = {}
        for window_type, window_start in windows.items():
            counter = UsageService.counter(user, window_type, window_start)
            window_payload[window_type] = {
                "chats": {
                    "used": counter.chats,
                    "limit": tier.entitlement("chats", window_type) if tier else None,
                    "remaining": (
                        max(0, tier.entitlement("chats", window_type) - counter.chats)
                        if tier and tier.entitlement("chats", window_type) is not None
                        else None
                    ),
                },
                "images": {
                    "used": counter.images,
                    "limit": tier.entitlement("images", window_type) if tier else None,
                    "remaining": (
                        max(0, tier.entitlement("images", window_type) - counter.images)
                        if tier and tier.entitlement("images", window_type) is not None
                        else None
                    ),
                },
                "premium_turns": {
                    "used": counter.premium_turns,
                    "limit": tier.entitlement(PREMIUM_ENTITLEMENT, window_type) if tier else None,
                    "remaining": (
                        max(0, tier.entitlement(PREMIUM_ENTITLEMENT, window_type) - counter.premium_turns)
                        if tier and tier.entitlement(PREMIUM_ENTITLEMENT, window_type) is not None
                        else None
                    ),
                },
                "tokens": {
                    "prompt": counter.prompt_tokens,
                    "completion": counter.completion_tokens,
                    "total": counter.prompt_tokens + counter.completion_tokens,
                },
                "cost_usd_micros": counter.cost_usd_micros,
            }

        day_state = cls.check(user, kind=UsageKind.CHAT, now=now)

        series = cls._series(user, now, days=days)

        return {
            "tier": {
                "code": tier.code if tier else None,
                "name": tier.name if tier else None,
                "rank": tier.rank if tier else None,
                "price_kes": float(tier.price_kes) if tier else 0,
                "is_free": (not tier) or tier.rank == 0,
            },
            "state": day_state.state,
            "reason": day_state.reason,
            "windows": window_payload,
            "resets_at": {
                "day": next_day_start(now).isoformat(),
                "week": next_week_start(now).isoformat(),
                "period": period_end.isoformat() if period_end else None,
            },
            "series": series,
            "entitlements": cls._entitlements(tier),
        }

    @classmethod
    def _series(cls, user, now: datetime, days: int = 14) -> List[Dict[str, Any]]:
        """Chats and images per local day, for the dashboard sparkline."""
        start = local_day_start(now) - timedelta(days=days - 1)
        rows = (
            UsageCounter.objects.filter(
                user=user, window_type=WindowType.DAY, window_start__gte=start
            )
            .order_by("window_start")
            .values("window_start", "chats", "images")
        )
        by_day = {
            timezone.localtime(row["window_start"]).date(): row for row in rows
        }
        series = []
        for offset in range(days):
            day = (start + timedelta(days=offset)).date()
            row = by_day.get(day)
            series.append(
                {
                    "date": day.isoformat(),
                    "chats": row["chats"] if row else 0,
                    "images": row["images"] if row else 0,
                }
            )
        return series

    @staticmethod
    def _entitlements(tier: Optional[Tier]) -> List[Dict[str, Any]]:
        """What the tier includes, for the dashboard's list."""
        labels = {
            "chats": "Chat turns",
            "images": "Image analysis",
            PREMIUM_ENTITLEMENT: "Premium model turns",
        }
        if tier is None:
            return []
        out = []
        for key, label in labels.items():
            day_limit = tier.entitlement(key, WindowType.DAY)
            week_limit = tier.entitlement(key, WindowType.WEEK)
            out.append(
                {
                    "key": key,
                    "label": label,
                    "per_day": day_limit,
                    "per_week": week_limit,
                    "included": bool(day_limit or week_limit),
                }
            )
        return out
