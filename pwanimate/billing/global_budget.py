"""
Account-wide daily budgets.

The per-user tier caps in :mod:`pwanimate.billing.services` bound what one
student may spend. They do not bound what the *account* spends -- and the
account's shared capacity is the thing that actually runs out. The Cloudflare
Workers AI free allocation (10,000 neurons/day) is shared by every user, so
a single burst of image analysis can exhaust it for everybody.

This module keeps one counter per metered kind, in Redis, and lets the
request path refuse work that would breach it.

The refusal is deliberately a *degradation*, not an error. When the budget
is gone, vision work fails with the honest "Spark Vision is down" notice and
the turn continues on the text-only path, so the student still gets an
answer instead of a wall.

Redis outages fail **open**. A budget that cannot be read must not take the
product down with it; the worst case of a blind budget is the thing it was
protecting.
"""

import logging
from datetime import timedelta
from typing import Optional

from django.conf import settings
from django.utils import timezone

logger = logging.getLogger(__name__)

BUDGET_KEY_PREFIX = "pwanimate:budget"


def _local_day(now=None):
    """Midnight of the local day -- matches the quota windows the UI shows."""
    now = now or timezone.now()
    return timezone.localtime(now).replace(hour=0, minute=0, second=0, microsecond=0)


class GlobalBudget:
    """
    One metered kind, one counter, one day.

    ``limit`` of ``None`` means unlimited -- the counter is not tracked at
    all, so the hot path does no Redis work in that case.
    """

    def __init__(self, kind: str, limit: Optional[int], now=None):
        self.kind = kind
        self.limit = int(limit) if limit is not None else None
        self.now = now

    # ------------------------------------------------------------------ keys
    @property
    def key(self) -> str:
        return f"{BUDGET_KEY_PREFIX}:{self.kind}:{_local_day(self.now).date().isoformat()}"

    @property
    def _ttl_seconds(self) -> int:
        """Live until the end of the local day, plus an hour of slack."""
        end = _local_day(self.now) + timedelta(days=1, hours=1)
        return max(60, int((end - timezone.localtime(timezone.now())).total_seconds()))

    # -------------------------------------------------------------- reading
    def used(self) -> int:
        if self.limit is None:
            return 0
        try:
            from pwaninet.redis_client import get_redis_client

            client = get_redis_client()
            raw = client.get(self.key)
            return int(raw) if raw is not None else 0
        except Exception:
            # Fail open: an unreadable budget is not a reason to stop work.
            logger.warning("Could not read global budget '%s'; treating as empty", self.kind)
            return 0

    def remaining(self) -> Optional[int]:
        """Remaining allowance, or ``None`` when unlimited."""
        if self.limit is None:
            return None
        return max(0, self.limit - self.used())

    def exhausted(self) -> bool:
        if self.limit is None:
            return False
        return self.used() >= self.limit

    # -------------------------------------------------------------- writing
    def consume(self, amount: int = 1) -> bool:
        """
        Claim ``amount`` from the budget.

        Returns ``True`` when the claim succeeded and ``False`` when it would
        have breached the limit. The increment and the limit check are a
        single atomic operation, so concurrent callers cannot both slip past
        the last unit.
        """
        if self.limit is None:
            return True
        try:
            from pwaninet.redis_client import get_redis_client

            client = get_redis_client()
            pipe = client.pipeline()
            pipe.incrby(self.key, amount)
            pipe.expire(self.key, self._ttl_seconds)
            used, _ = pipe.execute()
            if int(used) > self.limit:
                # Give the overage back so the counter reflects what was
                # actually served, not what was attempted.
                client.decrby(self.key, amount)
                return False
            return True
        except Exception:
            logger.warning("Could not consume global budget '%s'; allowing", self.kind)
            return True

    def reset(self) -> None:
        """Clear the counter (operations use after a manual reconciliation)."""
        try:
            from pwaninet.redis_client import get_redis_client

            get_redis_client().delete(self.key)
        except Exception:
            logger.warning("Could not reset global budget '%s'", self.kind)


def image_budget(now=None) -> GlobalBudget:
    """The account-wide daily budget for image analysis."""
    limit = getattr(settings, "PWANIMATE_GLOBAL_DAILY_IMAGE_BUDGET", None)
    if limit is not None:
        try:
            limit = int(limit)
        except (TypeError, ValueError):
            limit = None
        if limit is not None and limit <= 0:
            # 0 means "no budget configured", not "no images allowed".
            limit = None
    return GlobalBudget("image", limit, now=now)
