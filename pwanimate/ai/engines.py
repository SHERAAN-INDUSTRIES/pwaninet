"""
Spark Engine Registry.

User-facing "Spark family" engines (Spark Speed, Spark Coda, Spark Scholar,
Spark Vision) mapped onto ordered provider/model fallback chains. The picker
shows engines; the providers underneath are plumbing that can change without
touching the UI or the request contract.

Registry lives in code/settings for now (ships fast, zero migrations). The
billing / tiers design later promotes it to a DB table with min_tier fields
and server-side lock enforcement (see docs/local-models-design.md, D8).
"""

from typing import Any, Dict, List, Optional

from django.conf import settings

PROVIDER_LABELS = {
    "gemini": "Google",
    "groq": "Groq",
    "openrouter": "OpenRouter",
    "cloudflare": "Cloudflare",
    "mock": "Dev",
}


class UnknownSparkEngineError(Exception):
    """Raised when a request references an engine slug that does not exist (or is hidden)."""


def _load_engines() -> List[Dict[str, Any]]:
    configured = getattr(settings, "PWANIMATE_SPARK_ENGINES", None)
    if configured is not None:
        return [dict(engine) for engine in configured]

    gemini_model = getattr(settings, "PWANIMATE_GEMINI_MODEL", "gemini-3.8-flash")
    groq_model = getattr(settings, "PWANIMATE_GROQ_MODEL", "openai/gpt-oss-120b")
    openrouter_model = getattr(settings, "PWANIMATE_OPENROUTER_MODEL", "nvidia/nemotron-3-ultra-550b-a55b:free")
    vision_model = getattr(settings, "PWANIMATE_VISION_MODEL", "gemini-3.8-flash")
    cloudflare_vision_model = getattr(
        settings, "PWANIMATE_CLOUDFLARE_VISION_MODEL", "@cf/meta/llama-3.2-11b-vision-instruct"
    )
    cloudflare_vision_backup_model = getattr(
        settings, "PWANIMATE_CLOUDFLARE_VISION_BACKUP_MODEL", "@cf/meta/llama-4-scout-17b-16e-instruct"
    )

    engines: List[Dict[str, Any]] = [
        {
            "slug": "speed",
            "name": "Spark Speed",
            "icon": "bi-lightning-charge",
            "tagline": "Rapid everyday Q&A and chat",
            "description": "Fast, balanced answers for quick questions — Spark's everyday workhorse with automatic multi-provider failover.",
            "hidden": False,
            "fallback_chain": [
                {"provider": "gemini", "model": gemini_model},
                {"provider": "groq", "model": groq_model},
                {"provider": "openrouter", "model": openrouter_model},
                # Resilience ladder: known-good IDs from the global Auto chain,
                # interleaved across providers so one provider outage never
                # drains the engine. Ends in mock:unavailable (friendly error
                # instead of a raw 502); the status rollup ignores that rung.
                {"provider": "gemini", "model": "gemini-3.6-flash"},
                {"provider": "groq", "model": "qwen/qwen3.8-27b"},
                {"provider": "openrouter", "model": "nvidia/nemotron-3.5-lightning:free"},
                {"provider": "gemini", "model": "gemini-3.5-flash-lite"},
                {"provider": "groq", "model": "openai/gpt-oss-20b"},
                {"provider": "openrouter", "model": "liquid/lfm-2.5-2.6b:free"},
                {"provider": "gemini", "model": "gemini-flash-lite-latest"},
                {"provider": "openrouter", "model": "nvidia/nemotron-3-super-120b-a12b:free"},
                {"provider": "mock", "model": "unavailable"},
            ],
        },
        {
            "slug": "coda",
            "name": "Spark Coda",
            "icon": "bi-terminal",
            "tagline": "Code debugging and explanations",
            "description": "Reads, traces and fixes code — line-by-line debugging help for assignments and past papers.",
            "hidden": False,
            "fallback_chain": [
                {"provider": "groq", "model": groq_model},
                {"provider": "gemini", "model": gemini_model},
                {"provider": "openrouter", "model": openrouter_model},
                # Code-quality ladder: capable coders first (Qwen, gpt-oss-20b);
                # tiny models only as true last resorts — liquid-2.6B is
                # deliberately absent from Coda.
                {"provider": "groq", "model": "qwen/qwen3.8-27b"},
                {"provider": "gemini", "model": "gemini-3.6-flash"},
                {"provider": "groq", "model": "openai/gpt-oss-20b"},
                {"provider": "openrouter", "model": "nvidia/nemotron-3.5-lightning:free"},
                {"provider": "gemini", "model": "gemini-3.5-flash-lite"},
                {"provider": "openrouter", "model": "nvidia/nemotron-3-super-120b-a12b:free"},
                {"provider": "mock", "model": "unavailable"},
            ],
        },
        {
            "slug": "scholar",
            "name": "Spark Scholar",
            "icon": "bi-journal-richtext",
            "tagline": "Lecture slides, notes & quiz synthesis",
            "description": "Long-context reading of course documents — summaries, revision guides and quiz generation.",
            "hidden": False,
            "fallback_chain": [
                {"provider": "gemini", "model": gemini_model},
                {"provider": "openrouter", "model": openrouter_model},
                {"provider": "groq", "model": groq_model},
                # Long-context quality first; lite/tiny models only late.
                {"provider": "gemini", "model": "gemini-3.6-flash"},
                {"provider": "openrouter", "model": "nvidia/nemotron-3.5-lightning:free"},
                {"provider": "gemini", "model": "gemini-flash-latest"},
                {"provider": "groq", "model": "qwen/qwen3.8-27b"},
                {"provider": "openrouter", "model": "nvidia/nemotron-3-super-120b-a12b:free"},
                {"provider": "gemini", "model": "gemini-3.5-flash-lite"},
                {"provider": "groq", "model": "openai/gpt-oss-20b"},
                {"provider": "openrouter", "model": "liquid/lfm-2.5-2.6b:free"},
                {"provider": "mock", "model": "unavailable"},
            ],
        },
        {
            "slug": "vision",
            "name": "Spark Vision",
            "icon": "bi-eye",
            "tagline": "Images, OCR and whiteboard math",
            "description": "Reads uploaded images — timetables, circuit diagrams and handwritten working.",
            # The image route.
            #
            # Cloudflare Workers AI leads because it is the only vision
            # provider that belongs to *our* account: its free daily
            # allocation cannot be revoked by a third party, which is what
            # the free tier is allowed to depend on (D2). It costs a
            # fraction of a cent per image.
            #
            # Gemini stays as the paid backup and Groq as the last resort
            # (it accepts at most three images per turn).
            "hidden": False,
            "fallback_chain": [
                {"provider": "cloudflare", "model": cloudflare_vision_model},
                {"provider": "cloudflare", "model": cloudflare_vision_backup_model},
                {"provider": "gemini", "model": vision_model},
                {"provider": "groq", "model": "qwen/qwen3.8-27b"},
            ],
        },
    ]

    overrides = getattr(settings, "PWANIMATE_ENGINE_OVERRIDES", None) or {}
    for engine in engines:
        engine_override = overrides.get(engine["slug"])
        if isinstance(engine_override, dict):
            engine.update(engine_override)
    return engines


def list_engines(include_hidden: bool = False) -> List[Dict[str, Any]]:
    """Return the selectable Spark engines (hidden engines excluded by default)."""
    engines = _load_engines()
    if not include_hidden:
        engines = [engine for engine in engines if not engine.get("hidden")]
    return engines


def get_engine(slug: Optional[str]) -> Dict[str, Any]:
    """
    Resolve a selectable engine by slug.

    Raises UnknownSparkEngineError for missing, malformed, or hidden slugs —
    a hidden engine must behave as if it does not exist.
    """
    normalized = (slug or "").strip().lower()
    if not normalized:
        raise UnknownSparkEngineError("Engine slug is required.")
    for engine in list_engines(include_hidden=False):
        if engine["slug"] == normalized:
            return engine
    raise UnknownSparkEngineError(f"Unknown engine '{normalized}'.")


def _provider_configured(provider: str) -> bool:
    key_settings = {
        "gemini": "PWANIMATE_GEMINI_API_KEY",
        "groq": "PWANIMATE_GROQ_API_KEY",
        "openrouter": "PWANIMATE_OPENROUTER_API_KEY",
    }
    if provider == "mock":
        return True
    if provider == "cloudflare":
        # Cloudflare is configured by an AI-scoped token plus the account id;
        # there is no separate "model key".
        return bool(
            str(getattr(settings, "PWANIMATE_CLOUDFLARE_API_TOKEN", "") or "").strip()
            and str(getattr(settings, "PWANIMATE_CLOUDFLARE_ACCOUNT_ID", "") or "").strip()
        )
    setting_name = key_settings.get(provider)
    if not setting_name:
        return False
    return bool(str(getattr(settings, setting_name, "") or "").strip())


def get_engines_status(tracker: Any = None) -> List[Dict[str, Any]]:
    """
    Roll provider health up per engine for the picker.

    Each entry carries both engine-level presentation fields (name, tagline,
    powered_by, status) and legacy-shaped fields (is_configured, rate_limited,
    cooldown_remaining) so existing dot/badge rendering keeps working.
    """
    if tracker is None:
        from pwanimate.ai.gateway.quota_tracker import get_quota_tracker
        tracker = get_quota_tracker()

    entries: List[Dict[str, Any]] = []
    for engine in list_engines():
        chain = engine.get("fallback_chain") or []
        primary = chain[0] if chain else {"provider": "", "model": None}

        primary_configured = bool(primary["provider"]) and _provider_configured(primary["provider"])
        cooldown = (
            tracker.get_cooldown_remaining(primary["provider"], primary["model"])
            if primary_configured
            else 0.0
        )

        usable = [
            candidate
            for candidate in chain
            # `mock` is a friendly-error rung, not a real model: it must never
            # prop up the indicator (is_configured / fallbacks_ready).
            if candidate["provider"] != "mock"
            and _provider_configured(candidate["provider"])
            and not tracker.is_rate_limited(candidate["provider"], candidate["model"])
        ]
        primary_usable = any(
            candidate["provider"] == primary["provider"]
            and candidate["model"] == primary["model"]
            for candidate in usable
        )

        powered_by: List[str] = []
        for candidate in chain:
            if candidate["provider"] == "mock":
                continue
            label = PROVIDER_LABELS.get(candidate["provider"], str(candidate["provider"]).title())
            if label and label not in powered_by:
                powered_by.append(label)

        entries.append({
            "slug": engine["slug"],
            "name": engine["name"],
            "icon": engine.get("icon", "bi-cpu"),
            "tagline": engine.get("tagline", ""),
            "description": engine.get("description", ""),
            "powered_by": powered_by,
            "primary_provider": primary["provider"],
            "primary_model": primary["model"],
            # Legacy-shaped fields (existing UI dot/badge semantics):
            # rate_limited == "primary candidate not usable right now".
            # primary_status distinguishes WHY: a missing API key is not the
            # same situation as a live quota cooldown.
            "primary_status": (
                "ready" if primary_usable
                else ("unconfigured" if not primary_configured else "rate_limited")
            ),
            "is_configured": len(usable) > 0,
            "rate_limited": not primary_usable,
            "cooldown_remaining": cooldown if not primary_usable else 0.0,
            "fallbacks_ready": max(0, len(usable) - (1 if primary_usable else 0)),
        })
    return entries


def identify_engine(provider: Optional[str], model: Optional[str]) -> Optional[str]:
    """
    Brand-level attribution for the (provider, model) pair that served a reply.

    Returns the engine display name (e.g. "Spark Speed") or None, so the UI
    can credit an engine instead of leaking raw model identities. Primary
    matches win; otherwise the first engine whose ladder contains the pair
    (registry order). The `mock` rung never attributes to an engine.
    """
    if not provider or not model:
        return None
    if provider == "mock":
        return None
    key = (provider, str(model).strip().lower())
    engines = list_engines()
    for engine in engines:
        chain = engine.get("fallback_chain") or []
        if chain:
            primary = chain[0]
            if (primary["provider"], str(primary.get("model") or "").strip().lower()) == key:
                return engine["name"]
    for engine in engines:
        for candidate in engine.get("fallback_chain") or []:
            if (candidate["provider"], str(candidate.get("model") or "").strip().lower()) == key:
                return engine["name"]
    return None
