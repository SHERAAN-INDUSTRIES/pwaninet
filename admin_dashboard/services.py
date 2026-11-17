"""
Command-center data services.

Everything the redesigned dashboard renders is assembled here so the views
stay thin and the queries are reusable (context rail + centre panel read the
same helpers).

Three sources of truth are joined on purpose:

* ``pwanimate.ai.engines`` — the code ladders (what Spark *can* reach),
* ``pwanimate.billing.ModelOffering`` — the registry (what is priced, gated
  and switchable from this dashboard),
* ``pwanimate.billing.UsageRecord`` — the append-only metering ledger
  (tokens, cost, per provider/model).

Provider health comes from ``QuotaTracker`` (process-local rate-limit state)
and from settings (API key presence). Nothing here writes to the AI path
except :func:`set_model_active`.
"""

from datetime import timedelta
from typing import Any, Dict, List, Optional, Tuple

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.paginator import Paginator
from django.db.models import (
    Case, CharField, Count, F, IntegerField, Max, OuterRef, Q, Subquery, Sum, Value, When,
)
from django.db.models.functions import Coalesce, TruncDate
from django.utils import timezone

from .models import FeedbackTicket, UserDailyActivity

User = get_user_model()

# Engine icons arrive as Bootstrap names from ``pwanimate.ai.engines``; the
# workspace renders Lucide, so each row also carries an ``icon_lucide`` name.
_ENGINE_ICON_LUCIDE: Dict[str, str] = {
    "bi-lightning-charge": "zap",
    "bi-terminal": "square-terminal",
    "bi-journal-richtext": "notebook-text",
    "bi-eye": "eye",
    "bi-cpu": "cpu",
}


# --------------------------------------------------------------------------- providers
# Keys are env-only by design (they never leave the server), so the dashboard
# reports presence, never values.
PROVIDER_KEY_SETTINGS: Dict[str, Tuple[str, ...]] = {
    "gemini": ("PWANIMATE_GEMINI_API_KEY",),
    "groq": ("PWANIMATE_GROQ_API_KEY",),
    "openrouter": ("PWANIMATE_OPENROUTER_API_KEY",),
    "cloudflare": ("PWANIMATE_CLOUDFLARE_API_TOKEN", "PWANIMATE_CLOUDFLARE_ACCOUNT_ID"),
    "zai": ("ZAI_API_KEY",),
    "alibaba": ("ALIBABA_DASHSCOPE_API_KEY",),
    "tencent": ("TENCENT_TOKENHUB_API_KEY",),
    "deepseek": ("DEEPSEEK_API_KEY",),
}

# Providers that need no credentials at all.
PROVIDER_LOCAL: Dict[str, str] = {
    "ollama": "Local Ollama endpoint — no API key required.",
    "mock": "Mock provider — friendly error rung, never a real model.",
}

PROVIDER_ORDER = [
    "gemini", "groq", "openrouter", "zai", "alibaba", "tencent",
    "deepseek", "cloudflare", "ollama", "mock",
]


def provider_label(provider: str) -> str:
    try:
        from pwanimate.ai.engines import PROVIDER_LABELS
        return PROVIDER_LABELS.get(provider) or (provider or "").title()
    except Exception:  # pragma: no cover - label lookup must never break a page
        return (provider or "").title()


def provider_key_state(provider: str) -> Dict[str, Any]:
    """Is this provider configured? Reports presence only — never the value."""
    if provider in PROVIDER_LOCAL:
        return {"state": "local", "configured": True, "detail": PROVIDER_LOCAL[provider]}
    setting_names = PROVIDER_KEY_SETTINGS.get(provider)
    if setting_names is None:
        return {"state": "unknown", "configured": False, "detail": "No key setting is defined for this provider."}
    missing = [name for name in setting_names if not str(getattr(settings, name, "") or "").strip()]
    if missing:
        return {"state": "missing", "configured": False, "detail": "Missing " + ", ".join(missing)}
    return {"state": "ready", "configured": True, "detail": "Configured"}


def provider_health() -> List[Dict[str, Any]]:
    """One row per supported provider: key state + live rate-limit picture."""
    from pwanimate.ai.gateway.quota_tracker import get_quota_tracker

    tracker = get_quota_tracker()
    status = tracker.get_status()
    by_provider: Dict[str, List[Dict[str, Any]]] = {}
    for entry in status.get("models", []):
        by_provider.setdefault(entry.get("provider", ""), []).append(entry)

    providers = list(PROVIDER_ORDER)
    for provider in by_provider:
        if provider not in providers:
            providers.append(provider)

    rows: List[Dict[str, Any]] = []
    for provider in providers:
        if not provider:
            continue
        entries = by_provider.get(provider, [])
        limited = [e for e in entries if e.get("rate_limited")]
        cooldowns = [e.get("cooldown_remaining") or 0 for e in entries]
        key = provider_key_state(provider)
        if limited:
            state = "rate_limited"
        elif not key["configured"]:
            state = "not_configured"
        elif entries:
            state = "healthy"
        else:
            state = "idle"
        rows.append({
            "provider": provider,
            "label": provider_label(provider),
            "state": state,
            "configured": key["configured"],
            "key_state": key["state"],
            "key_detail": key["detail"],
            "chain_models": len(entries),
            "rate_limited_models": len(limited),
            "cooldown_remaining": max(cooldowns) if cooldowns else 0,
            "models": entries,
        })
    return rows


def engine_health() -> List[Dict[str, Any]]:
    """Per-engine rollup (status / primary / powered_by / cooldown)."""
    from pwanimate.ai.engines import get_engines_status

    rows = get_engines_status()
    for row in rows:
        row["icon_lucide"] = _ENGINE_ICON_LUCIDE.get(row.get("icon"), "cpu")
    return rows


# --------------------------------------------------------------------------- catalog
def _engine_ladders() -> Dict[Tuple[str, str], Dict[str, Any]]:
    """(provider, model) -> where it sits in the code ladders."""
    from pwanimate.ai.engines import list_engines

    rungs: Dict[Tuple[str, str], Dict[str, Any]] = {}
    for engine in list_engines(include_hidden=True):
        for position, rung in enumerate(engine.get("fallback_chain") or []):
            provider = (rung.get("provider") or "").strip().lower()
            model = (rung.get("model") or "").strip()
            if not provider:
                continue
            row = rungs.setdefault((provider, model), {"engines": [], "primary_of": [], "positions": []})
            row["engines"].append({
                "slug": engine.get("slug"),
                "name": engine.get("name"),
                "hidden": bool(engine.get("hidden")),
            })
            row["positions"].append(position)
            if position == 0:
                row["primary_of"].append(engine.get("name"))
    return rungs


def _global_chain_pairs() -> set:
    pairs = set()
    for item in getattr(settings, "PWANIMATE_LLM_FALLBACK_CHAIN", []) or []:
        if isinstance(item, dict):
            provider = (item.get("provider") or "").strip().lower()
            model = (item.get("model") or "").strip()
        else:
            text = str(item)
            provider, model = (text.split(":", 1) if ":" in text else (text, ""))
            provider, model = provider.strip().lower(), model.strip()
        if provider:
            pairs.add((provider, model))
    return pairs


def model_catalog() -> List[Dict[str, Any]]:
    """
    Every model the platform can name, with its live state.

    ``status`` is one of: ``active`` · ``rate_limited`` · ``no_key`` ·
    ``disabled`` · ``unregistered``. Disabled rows are rendered inert by the
    template — the state is computed here so the context rail and the centre
    panel agree.
    """
    from pwanimate.ai.gateway.quota_tracker import get_quota_tracker
    from pwanimate.billing.models import ModelOffering

    tracker = get_quota_tracker()
    offerings = {
        (o.provider, o.model_id): o
        for o in ModelOffering.objects.select_related("min_tier").all()
    }
    ladders = _engine_ladders()
    global_chain = _global_chain_pairs()

    rows: Dict[Tuple[str, str], Dict[str, Any]] = {}

    def touch(provider: str, model: str) -> Dict[str, Any]:
        key = (provider, model)
        if key not in rows:
            rows[key] = {
                "provider": provider,
                "provider_label": provider_label(provider),
                "model_id": model,
                "engines": [],
                "primary_of": [],
                "in_global_chain": False,
                "in_ladder": False,
            }
        return rows[key]

    for (provider, model), info in ladders.items():
        row = touch(provider, model)
        row["engines"] = info["engines"]
        row["primary_of"] = info["primary_of"]
        row["in_ladder"] = True

    for provider, model in global_chain:
        touch(provider, model)["in_global_chain"] = True

    for (provider, model), offering in offerings.items():
        touch(provider, model)

    # QuotaTracker reports live cooldowns per (provider, model) in the global
    # chain; resolve defensively on both fields so two providers' ids never
    # collide.
    live_models = tracker.get_status().get("models", [])

    catalog: List[Dict[str, Any]] = []
    for row in rows.values():
        provider = row["provider"]
        model = row["model_id"]
        offering = offerings.get((provider, model))
        key = provider_key_state(provider)
        live = next(
            (e for e in live_models
             if e.get("provider") == provider and e.get("model") == model),
            None,
        )
        rate_limited = bool(live and live.get("rate_limited"))
        cooldown = float(live.get("cooldown_remaining") or 0) if live else 0.0

        registered = offering is not None
        disabled = bool(offering is not None and not offering.is_active)

        if disabled:
            status, status_label = "disabled", "Disabled"
        elif rate_limited:
            status, status_label = "rate_limited", "Rate limited"
        elif not key["configured"]:
            status, status_label = "no_key", "No API key"
        elif registered and offering.is_active:
            status, status_label = "active", "Active"
        else:
            # Reachable through code/settings but never priced or gated —
            # the registry (seed_model_registry) is what makes it manageable.
            status, status_label = "unregistered", "Not in registry"

        row.update({
            "offering": offering,
            "registered": registered,
            "disabled": disabled,
            "rate_limited": rate_limited,
            "cooldown_remaining": cooldown,
            "configured": key["configured"],
            "key_detail": key["detail"],
            "status": status,
            "status_label": status_label,
            "min_tier": offering.min_tier if offering else None,
            "min_tier_name": (offering.min_tier.name if offering and offering.min_tier else None),
            "cost_class": offering.get_cost_class_display() if offering else None,
            "toggleable": registered,
        })
        catalog.append(row)

    status_rank = {"active": 0, "rate_limited": 1, "unregistered": 2, "no_key": 3, "disabled": 4}
    catalog.sort(key=lambda r: (status_rank.get(r["status"], 9), r["provider"], r["model_id"]))
    return catalog


def set_model_active(provider: str, model_id: str, is_active: bool):
    """
    Switch a registered model on or off.

    Returns ``(offering, error)``. Unregistered models are refused: enabling
    one would publish it to the free tier with no price and no ``min_tier``,
    so registration stays a deliberate act (``seed_model_registry``).
    """
    from pwanimate.billing.models import ModelOffering
    from pwanimate.billing.services import invalidate_offering_index

    offering = ModelOffering.objects.filter(provider=provider, model_id=model_id).first()
    if offering is None:
        return None, "not_registered"
    if offering.is_active != is_active:
        offering.is_active = is_active
        offering.save(update_fields=["is_active"])
        invalidate_offering_index()
    return offering, None


# --------------------------------------------------------------------------- usage
def token_usage(days: int = 30) -> Dict[str, Any]:
    """Token/cost usage from the metering ledger over the last N days."""
    from pwanimate.billing.models import UsageRecord

    today = timezone.localdate()
    start = today - timedelta(days=days - 1)
    qs = UsageRecord.objects.filter(created_at__date__gte=start)

    totals = qs.aggregate(
        requests=Count("id"),
        prompt=Sum("prompt_tokens"),
        completion=Sum("completion_tokens"),
        total=Sum("total_tokens"),
        cost_micros=Sum("cost_usd_micros"),
        images=Sum("images"),
        users=Count("user", distinct=True),
    )

    daily_rows = (
        qs.annotate(day=TruncDate("created_at"))
        .values("day")
        .annotate(
            prompt=Sum("prompt_tokens"),
            completion=Sum("completion_tokens"),
            total=Sum("total_tokens"),
            cost_micros=Sum("cost_usd_micros"),
            requests=Count("id"),
        )
        .order_by("day")
    )
    daily_by_day = {row["day"]: row for row in daily_rows}
    labels, prompt_data, completion_data, total_data, request_data, cost_data = [], [], [], [], [], []
    for offset in range(days):
        day = start + timedelta(days=offset)
        row = daily_by_day.get(day) or {}
        labels.append(day.strftime("%b %d"))
        prompt_data.append(row.get("prompt") or 0)
        completion_data.append(row.get("completion") or 0)
        total_data.append(row.get("total") or 0)
        request_data.append(row.get("requests") or 0)
        cost_data.append(round((row.get("cost_micros") or 0) / 1_000_000.0, 6))

    by_model = list(
        qs.values("provider", "model_id")
        .annotate(
            total=Sum("total_tokens"),
            prompt=Sum("prompt_tokens"),
            completion=Sum("completion_tokens"),
            cost_micros=Sum("cost_usd_micros"),
            requests=Count("id"),
            users=Count("user", distinct=True),
        )
        .order_by("-total")[:25]
    )
    for row in by_model:
        row["cost_usd"] = round((row.get("cost_micros") or 0) / 1_000_000.0, 6)
        row["label"] = row.get("model_id") or row.get("provider") or "unknown"

    by_provider = list(
        qs.values("provider")
        .annotate(
            total=Sum("total_tokens"),
            requests=Count("id"),
            cost_micros=Sum("cost_usd_micros"),
            users=Count("user", distinct=True),
        )
        .order_by("-total")
    )
    for row in by_provider:
        row["cost_usd"] = round((row.get("cost_micros") or 0) / 1_000_000.0, 6)

    return {
        "days": days,
        "start": start,
        "labels": labels,
        "prompt": prompt_data,
        "completion": completion_data,
        "total": total_data,
        "requests": request_data,
        "cost": cost_data,
        "by_model": by_model,
        "by_provider": by_provider,
        "totals": {
            "requests": totals.get("requests") or 0,
            "prompt": totals.get("prompt") or 0,
            "completion": totals.get("completion") or 0,
            "total": totals.get("total") or 0,
            "images": totals.get("images") or 0,
            "users": totals.get("users") or 0,
            "cost_usd": round((totals.get("cost_micros") or 0) / 1_000_000.0, 6),
        },
    }


def model_usage_map(days: int = 30) -> Dict[Tuple[str, str], Dict[str, Any]]:
    """(provider, model_id) -> usage over N days, for annotating the catalog."""
    from pwanimate.billing.models import UsageRecord

    start = timezone.localdate() - timedelta(days=days - 1)
    rows = (
        UsageRecord.objects.filter(created_at__date__gte=start)
        .values("provider", "model_id")
        .annotate(
            total=Sum("total_tokens"),
            requests=Count("id"),
            users=Count("user", distinct=True),
            last_used=Max("created_at"),
        )
    )
    return {(row["provider"], row["model_id"]): row for row in rows}


# --------------------------------------------------------------------------- overview
def platform_overview(days: int = 14) -> Dict[str, Any]:
    """Headline metrics + 14-day series for the overview panel."""
    from posts.models import Report

    now = timezone.now()
    today = timezone.localdate(now)
    active_since = now - timedelta(minutes=5)

    unread_feedback = FeedbackTicket.objects.filter(status=FeedbackTicket.Status.UNREAD).count()
    open_tickets = FeedbackTicket.objects.filter(
        status__in=[FeedbackTicket.Status.OPEN, FeedbackTicket.Status.IN_PROGRESS]
    ).count()
    total_users = User.objects.count()
    pending_reports = Report.objects.filter(status=Report.Status.PENDING).count()
    online_users = UserDailyActivity.objects.filter(
        activity_date=today, last_seen_at__gte=active_since
    ).values("user_id").distinct().count()
    daily_active = UserDailyActivity.objects.filter(
        activity_date=today
    ).values("user_id").distinct().count()

    start = today - timedelta(days=days - 1)
    growth_rows = (
        User.objects.filter(date_joined__date__gte=start)
        .annotate(day=TruncDate("date_joined", tzinfo=timezone.get_current_timezone()))
        .values("day")
        .annotate(count=Count("id"))
    )
    growth_by_day = {row["day"]: row["count"] for row in growth_rows}
    active_rows = (
        UserDailyActivity.objects.filter(activity_date__gte=start, activity_date__lte=today)
        .values("activity_date")
        .annotate(count=Count("user_id", distinct=True))
    )
    active_by_day = {row["activity_date"]: row["count"] for row in active_rows}

    labels, growth, active = [], [], []
    for offset in range(days):
        day = start + timedelta(days=offset)
        labels.append(day.strftime("%b %d"))
        growth.append(growth_by_day.get(day, 0))
        active.append(active_by_day.get(day, 0))

    return {
        "total_users": total_users,
        "online_users": online_users,
        "daily_active_users": daily_active,
        "pending_reports": pending_reports,
        "unread_feedback_count": unread_feedback,
        "open_tickets_count": open_tickets,
        "recent_feedback": FeedbackTicket.objects.filter(
            status=FeedbackTicket.Status.UNREAD
        ).select_related("sender")[:5],
        "chart_labels": labels,
        "growth_data": growth,
        "active_data": active,
    }


def registry_health() -> Dict[str, Any]:
    """Is the model registry wired up? (Toggles are no-ops without it.)"""
    from pwanimate.billing.models import ModelOffering, Tier

    offerings = ModelOffering.objects.count()
    disabled = ModelOffering.objects.filter(is_active=False).count()
    default_tier = Tier.objects.filter(is_default=True, is_active=True).exists()
    any_tier = Tier.objects.filter(is_active=True).exists()
    return {
        "offerings": offerings,
        "disabled": disabled,
        "default_tier": default_tier,
        "any_tier": any_tier,
        "healthy": offerings > 0 and default_tier,
    }


def ops_summary() -> Dict[str, Any]:
    """Compact figures for the right rail (live operations)."""
    now = timezone.now()
    today = timezone.localdate(now)
    catalog = model_catalog()
    counts = {"active": 0, "disabled": 0, "rate_limited": 0, "no_key": 0, "unregistered": 0}
    for row in catalog:
        counts[row["status"]] = counts.get(row["status"], 0) + 1

    provider_rows = provider_health()
    provider_alerts = sum(
        1 for row in provider_rows if row["state"] in ("not_configured", "rate_limited")
    )
    return {
        "provider_alerts": provider_alerts,
        "online_users": UserDailyActivity.objects.filter(
            activity_date=today, last_seen_at__gte=now - timedelta(minutes=5)
        ).values("user_id").distinct().count(),
        "unread_feedback": FeedbackTicket.objects.filter(status=FeedbackTicket.Status.UNREAD).count(),
        "open_tickets": FeedbackTicket.objects.filter(
            status__in=[FeedbackTicket.Status.OPEN, FeedbackTicket.Status.IN_PROGRESS]
        ).count(),
        "model_counts": counts,
        "model_total": len(catalog),
    }


# --------------------------------------------------------------------------- login audit
def _parse_ua(user_agent_string: str) -> Dict[str, str]:
    """Browser/OS/device for a raw user agent (blank fields if unparsable)."""
    try:
        from user_agents import parse
        ua = parse(user_agent_string or '')
        return {
            'browser': ua.browser.family or 'Unknown',
            'browser_version': ua.browser.version_string or '',
            'os': (ua.os.family + ((' ' + ua.os.version_string) if ua.os.version_string else '')) or 'Unknown',
            'device': ua.device.family or 'Unknown',
            'is_mobile': 'yes' if ua.is_mobile else 'no',
        }
    except Exception:
        return {'browser': 'Unknown', 'browser_version': '', 'os': 'Unknown', 'device': 'Unknown', 'is_mobile': 'no'}


def _session_location(username: str) -> str:
    if not username:
        return ''
    try:
        from users.models import UserSession
        sess = UserSession.objects.filter(user__username=username).order_by('-last_activity').first()
        return (sess.location or '').strip() if sess else ''
    except Exception:
        return ''


def recent_logins(days: int = 7, outcome: str = 'all', search: str = '') -> List[Dict[str, Any]]:
    """
    Merge the login-looking evidence from three sources into one audit feed:

    * ``axes.AccessLog`` — successful accesses,
    * ``axes.AccessFailureLog`` — failed / blocked attempts,
    * ``users.DeviceVerificationEvent`` — sign-ins from never-trusted devices.

    Ordered newest-first. Location comes from the session record of the
    account (success) or the challenge event (new device); failed attempts
    keep the axes UA/IP but carry no location of their own.
    """
    from axes.models import AccessLog, AccessFailureLog
    from users.models import DeviceVerificationEvent

    cutoff = timezone.now() - timedelta(days=days)
    rows: List[Dict[str, Any]] = []

    if outcome in ('all', 'success'):
        for r in AccessLog.objects.filter(attempt_time__gte=cutoff).order_by('-attempt_time')[:150]:
            meta = _parse_ua(r.user_agent or '')
            rows.append({
                'when': r.attempt_time,
                'user': r.username or '(unknown)',
                'ip': r.ip_address or '',
                'location': _session_location(r.username),
                'browser': f"{meta['browser']} {meta['browser_version']}".strip(),
                'os_device': f"{meta['os']} · {meta['device']}",
                'outcome': 'success',
                'outcome_label': 'Success',
                'source': 'session',
            })

    if outcome in ('all', 'failed'):
        for r in AccessFailureLog.objects.filter(attempt_time__gte=cutoff).order_by('-attempt_time')[:150]:
            meta = _parse_ua(r.user_agent or '')
            locked = bool(r.locked_out)
            rows.append({
                'when': r.attempt_time,
                'user': r.username or '(unknown)',
                'ip': r.ip_address or '',
                'location': '',
                'browser': f"{meta['browser']} {meta['browser_version']}".strip(),
                'os_device': f"{meta['os']} · {meta['device']}",
                'outcome': 'blocked' if locked else 'failed',
                'outcome_label': 'Blocked' if locked else 'Failed',
                'source': 'axes',
            })

    if outcome in ('all', 'new_device'):
        for r in DeviceVerificationEvent.objects.filter(created_at__gte=cutoff).select_related('user').order_by('-created_at')[:150]:
            loc = ', '.join(p for p in (r.location_city, r.location_country) if p)
            rows.append({
                'when': r.created_at,
                'user': r.user.username if r.user_id else '(unknown)',
                'ip': str(r.ip_address or ''),
                'location': loc or 'Unknown (IP-only)',
                'browser': r.device_name or (r.browser or 'Unknown'),
                'os_device': f"{r.operating_system or 'Unknown'} · {r.device_type or 'unknown'}",
                'outcome': 'new_device',
                'outcome_label': 'New device',
                'source': 'device',
            })

    if search:
        s = search.strip().lower()
        rows = [
            r for r in rows
            if s in r['user'].lower() or s in (r['ip'] or '').lower()
        ]

    rows.sort(key=lambda r: r['when'] or timezone.now(), reverse=True)
    return rows[:200]


def admin_roster() -> List[Dict[str, Any]]:
    """Staff roster with session/2FA health for the admin manager."""
    from users.models import UserSession, UserTwoFactor

    now = timezone.now()
    today = timezone.localdate(now)

    online_ids = set(
        UserDailyActivity.objects.filter(
            activity_date=today, last_seen_at__gte=now - timedelta(minutes=5)
        ).values_list('user_id', flat=True)
    )
    daily_rows = {
        row['user_id']: row['last_seen_at']
        for row in UserDailyActivity.objects.filter(activity_date=today).values('user_id', 'last_seen_at')
    }

    staff = User.objects.filter(is_staff=True).order_by('-last_login', 'username')
    rows = []
    for u in staff:
        sessions = list(
            UserSession.objects.filter(user=u).order_by('-last_activity')
        )
        try:
            two = UserTwoFactor.objects.get(user=u)
            twofa = bool(two.is_enabled)
        except Exception:
            twofa = False
        current = next((s for s in sessions if s.is_current), None)
        pic = ''
        try:
            pic = (u.profile_pic.url if u.profile_pic else '')
        except Exception:
            pic = ''
        sessions_sorted = sorted(sessions, key=lambda s: s.last_activity or timezone.now(), reverse=True)
        latest = sessions_sorted[0] if sessions_sorted else None
        rows.append({
            'user': u,
            'username': u.username,
            'email': u.email,
            'profile_pic': pic,
            'is_superuser': u.is_superuser,
            'online': u.id in online_ids,
            'last_login': u.last_login,
            'last_seen': daily_rows.get(u.id),
            'sessions': len(sessions),
            'devices': len({s.device_name for s in sessions if s.device_name}),
            'two_factor': twofa,
            'current_device': getattr(current, 'device_name', '') if current else (getattr(latest, 'device_name', '') if latest else ''),
            'current_location': getattr(current, 'location', '') if current else (getattr(latest, 'location', '') if latest else ''),
            'current_browser': getattr(current, 'browser', '') if current else (getattr(latest, 'browser', '') if latest else ''),
            'current_os': getattr(current, 'operating_system', '') if current else (getattr(latest, 'operating_system', '') if latest else ''),
            'latest_user_agent': getattr(current, 'user_agent', '') if current else (getattr(latest, 'user_agent', '') if latest else ''),
            'session_rows': sessions,
        })
    return rows


# --------------------------------------------------------------------------- documents
DOCUMENT_INDEX_TABS = ('all', 'fully_indexed', 'partially_indexed', 'not_started')


def _classify_index_state(total, completed, active, failed, pending, processing):
    total = total or 0
    completed = completed or 0
    active = active or 0
    failed = failed or 0
    pending = pending or 0
    processing = processing or 0
    if total <= 0:
        return 'not_started'
    if (
        completed == total
        and active == total
        and failed == 0
        and pending == 0
        and processing == 0
    ):
        return 'fully_indexed'
    return 'partially_indexed'


def _index_state_label(state):
    return {
        'fully_indexed': 'Fully indexed',
        'partially_indexed': 'Partially indexed',
        'not_started': 'Not started',
    }.get(state, 'Unknown')


def document_index_overview(
    *,
    tab='all',
    search='',
    doc_status='',
    visibility='',
    sort='newest',
    page=1,
    page_size=25,
):
    """Paginated document inventory with latest-version Spark indexing stats."""
    from documents.models import Document, DocumentVersion
    from pwanimate.models import DocumentChunk

    if tab not in DOCUMENT_INDEX_TABS:
        tab = 'all'
    try:
        page = max(1, int(page))
    except (TypeError, ValueError):
        page = 1
    try:
        page_size = min(max(10, int(page_size)), 100)
    except (TypeError, ValueError):
        page_size = 25

    latest_version_sq = DocumentVersion.objects.filter(
        document=OuterRef('pk'), is_latest=True
    ).values('id')[:1]

    qs = (
        Document.objects.all()
        .select_related('uploaded_by', 'category')
        .annotate(
            latest_version_id_annot=Subquery(latest_version_sq),
            views_count=Count('views', distinct=True),
            downloads_count=Count('downloads', distinct=True),
        )
    )

    search = (search or '').strip()
    if search:
        q_obj = (
            Q(title__icontains=search)
            | Q(slug__icontains=search)
            | Q(uploaded_by__username__icontains=search)
            | Q(uploaded_by__email__icontains=search)
        )
        if search.isdigit():
            q_obj |= Q(id=int(search))
        # share_id text match best-effort
        q_obj |= Q(share_id__icontains=search)
        qs = qs.filter(q_obj)
    if doc_status:
        qs = qs.filter(status=doc_status)
    if visibility:
        qs = qs.filter(visibility=visibility)

    sort_map = {
        'newest': '-created_at',
        'oldest': 'created_at',
        'updated': '-updated_at',
        'title': 'title',
        'most_viewed': '-views_count',
        'most_downloaded': '-downloads_count',
    }
    qs = qs.order_by(sort_map.get(sort, '-created_at'), '-id')

    docs = list(qs)
    version_ids = [d.latest_version_id_annot for d in docs if d.latest_version_id_annot]

    stats_by_version = {}
    models_by_version = {}
    if version_ids:
        chunk_rows = (
            DocumentChunk.objects.filter(document_version_id__in=version_ids)
            .values('document_version_id')
            .annotate(
                total=Count('id'),
                completed=Count('id', filter=Q(embedding_status='completed')),
                pending=Count('id', filter=Q(embedding_status='pending')),
                processing=Count('id', filter=Q(embedding_status='processing')),
                failed=Count('id', filter=Q(embedding_status='failed')),
                active=Count('id', filter=Q(is_active=True)),
            )
        )
        stats_by_version = {row['document_version_id']: row for row in chunk_rows}
        for row in (
            DocumentChunk.objects.filter(
                document_version_id__in=version_ids,
                embedding_model__gt='',
            )
            .exclude(embedding_status__in=['pending', 'processing'])
            .values('document_version_id', 'embedding_model')
            .annotate(count=Count('id'))
            .order_by('document_version_id', '-count')
        ):
            models_by_version.setdefault(row['document_version_id'], []).append(row)

    # Also capture any model labels present (including pending if set) as fallback
    if version_ids:
        for row in (
            DocumentChunk.objects.filter(
                document_version_id__in=version_ids,
                embedding_model__gt='',
            )
            .values('document_version_id', 'embedding_model')
            .annotate(count=Count('id'))
            .order_by('document_version_id', '-count')
        ):
            existing = models_by_version.setdefault(row['document_version_id'], [])
            if not any(x['embedding_model'] == row['embedding_model'] for x in existing):
                existing.append(row)

    summary = {
        'all': len(docs),
        'fully_indexed': 0,
        'partially_indexed': 0,
        'not_started': 0,
        'total_chunks': 0,
        'total_views': 0,
        'total_downloads': 0,
        'with_failures': 0,
    }

    enriched = []
    for doc in docs:
        vid = doc.latest_version_id_annot
        st = stats_by_version.get(vid, {}) if vid else {}
        total = st.get('total', 0) or 0
        completed = st.get('completed', 0) or 0
        pending = st.get('pending', 0) or 0
        processing = st.get('processing', 0) or 0
        failed = st.get('failed', 0) or 0
        active = st.get('active', 0) or 0
        state = _classify_index_state(total, completed, active, failed, pending, processing)
        models = models_by_version.get(vid, []) if vid else []
        model_labels = [m['embedding_model'] for m in models]
        primary_model = model_labels[0] if model_labels else ''

        row = {
            'document': doc,
            'latest_version_id': vid,
            'index_state': state,
            'index_state_label': _index_state_label(state),
            'chunks_total': total,
            'chunks_completed': completed,
            'chunks_pending': pending,
            'chunks_processing': processing,
            'chunks_failed': failed,
            'chunks_active': active,
            'embedding_models': model_labels,
            'embedding_model': primary_model,
            'views_count': getattr(doc, 'views_count', 0) or 0,
            'downloads_count': getattr(doc, 'downloads_count', 0) or 0,
        }
        summary[state] = summary.get(state, 0) + 1
        summary['total_chunks'] += total
        summary['total_views'] += row['views_count']
        summary['total_downloads'] += row['downloads_count']
        if failed:
            summary['with_failures'] += 1
        enriched.append(row)

    if tab != 'all':
        enriched = [r for r in enriched if r['index_state'] == tab]

    paginator = Paginator(enriched, page_size)
    page_obj = paginator.get_page(page)

    return {
        'page_obj': page_obj,
        'rows': list(page_obj.object_list),
        'summary': summary,
        'tab': tab,
        'search': search,
        'doc_status': doc_status,
        'visibility': visibility,
        'sort': sort,
        'page_size': page_size,
        'tabs': [
            ('all', 'All', summary.get('all', 0)),
            ('fully_indexed', 'Fully indexed', summary.get('fully_indexed', 0)),
            ('partially_indexed', 'Partially indexed', summary.get('partially_indexed', 0)),
            ('not_started', 'Not started', summary.get('not_started', 0)),
        ],
        'status_choices': getattr(Document, 'STATUS_CHOICES', []),
        'visibility_choices': getattr(Document, 'VISIBILITY_CHOICES', []),
        'sort_options': [
            ('newest', 'Newest'),
            ('oldest', 'Oldest'),
            ('updated', 'Recently updated'),
            ('title', 'Title A–Z'),
            ('most_viewed', 'Most viewed'),
            ('most_downloaded', 'Most downloaded'),
        ],
    }


def queue_document_reindex(document_id, *, mode='ingest'):
    """Queue ingestion or embedding for a document's latest version."""
    from documents.models import Document

    doc = Document.objects.filter(id=document_id).select_related('uploaded_by').first()
    if not doc:
        return None, 'not_found'
    version = doc.versions.filter(is_latest=True).order_by('-version_number').first() or doc.latest_version
    if not version:
        return None, 'no_latest_version'
    if mode not in ('ingest', 'embed'):
        mode = 'ingest'
    try:
        if mode == 'embed':
            from pwanimate.tasks.embeddings import embed_document_version
            result = embed_document_version.delay(version_id=version.id, force=True)
        else:
            from pwanimate.tasks.ingestion import ingest_document_version
            result = ingest_document_version.delay(version_id=version.id, force=True)
        task_id = getattr(result, 'id', None)
        return {'document': doc, 'version': version, 'task_id': task_id, 'mode': mode}, None
    except Exception as exc:
        return None, str(exc)
