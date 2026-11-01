"""
Provider Quota Tracker for Pwanimate.

Thread-safe, in-memory tracker recording rate-limit (429) events per provider.
Providers are considered rate-limited for a configurable cooldown period
after the last 429 response. Used by AIGateway to drive automatic fallback.
"""

import logging
import threading
import time
from typing import Any, Dict, List, Optional

from django.conf import settings

logger = logging.getLogger(__name__)

# Default cooldown in seconds after a 429 before retrying the same provider
DEFAULT_RATE_LIMIT_COOLDOWN = 60


class QuotaTracker:
    """
    In-memory tracker for provider rate-limit state.
    
    Thread-safe singleton accessible via get_quota_tracker().
    """

    def __init__(self, cooldown: Optional[float] = None):
        self._lock = threading.Lock()
        # provider_name -> monotonic timestamp of last 429
        self._rate_limit_hits: Dict[str, float] = {}
        self._cooldown = cooldown  # lazy-loaded from settings if None

    @property
    def cooldown(self) -> float:
        if self._cooldown is not None:
            return self._cooldown
        return float(getattr(settings, "PWANIMATE_RATE_LIMIT_COOLDOWN", DEFAULT_RATE_LIMIT_COOLDOWN))

    def _make_key(self, provider_name: str, model_name: Optional[str] = None) -> str:
        provider = provider_name.strip().lower()
        if model_name and str(model_name).strip():
            return f"{provider}:{str(model_name).strip().lower()}"
        return provider

    def record_rate_limit(self, provider_name: str, model_name: Optional[str] = None) -> None:
        """Record that a provider or specific model returned a rate-limit (429) response."""
        key = self._make_key(provider_name, model_name)
        with self._lock:
            self._rate_limit_hits[key] = time.monotonic()
            logger.warning(
                "Quota tracker: '%s' rate-limited, cooldown %.0fs",
                key,
                self.cooldown,
            )

    def is_rate_limited(self, provider_name: str, model_name: Optional[str] = None) -> bool:
        """
        Check if a provider or specific model is currently in cooldown from a 429.
        Returns True if either the specific model or the overall provider is rate-limited.
        """
        keys_to_check = [self._make_key(provider_name, model_name)]
        if model_name:
            keys_to_check.append(provider_name.strip().lower())

        with self._lock:
            now = time.monotonic()
            for key in keys_to_check:
                hit_time = self._rate_limit_hits.get(key)
                if hit_time is not None:
                    elapsed = now - hit_time
                    if elapsed >= self.cooldown:
                        self._rate_limit_hits.pop(key, None)
                    else:
                        return True
            return False

    def clear(self) -> None:
        """Clear all recorded rate limit hits (useful for testing)."""
        with self._lock:
            self._rate_limit_hits.clear()

    def clear_candidate(self, provider_name: str, model_name: Optional[str] = None) -> None:
        """
        Clear cooldown for a specific provider/model candidate.
        Used when a candidate fails with a transient error (e.g. connection
        failure) so it can be retried immediately on the next request.
        """
        keys_to_clear = [self._make_key(provider_name, model_name)]
        if model_name:
            keys_to_clear.append(provider_name.strip().lower())
        with self._lock:
            for key in keys_to_clear:
                self._rate_limit_hits.pop(key, None)

    def get_cooldown_remaining(
        self, provider_name: str, model_name: Optional[str] = None
    ) -> float:
        """Return remaining cooldown seconds, or 0.0 if not rate-limited."""
        keys_to_check = [self._make_key(provider_name, model_name)]
        if model_name:
            keys_to_check.append(provider_name.strip().lower())

        with self._lock:
            now = time.monotonic()
            max_remaining = 0.0
            for key in keys_to_check:
                hit_time = self._rate_limit_hits.get(key)
                if hit_time is not None:
                    remaining = self.cooldown - (now - hit_time)
                    if remaining > max_remaining:
                        max_remaining = remaining
            return max(0.0, round(max_remaining, 1))

    def get_status(self) -> Dict[str, Any]:
        """
        Return current quota status for all configured models in the fallback chain.
        """
        fallback_chain = getattr(
            settings,
            "PWANIMATE_LLM_FALLBACK_CHAIN",
            [
                {"provider": "gemini", "model": "gemini-3.8-flash"},
                {"provider": "groq", "model": "openai/gpt-oss-120b"},
                {"provider": "gemini", "model": "gemini-3.6-flash"},
                {"provider": "openrouter", "model": "nvidia/nemotron-3-ultra-550b-a55b:free"},
            ],
        )

        MODEL_METADATA = {
            # Google Gemini (Google AI Studio API)
            ("gemini", "gemini-3.8-flash"): {
                "display_name": "Gemini 3.8 Flash",
                "provider_label": "Google",
                "description": "Google's latest flagship high-efficiency multimodal model",
            },
            ("gemini", "gemini-3.7-flash"): {
                "display_name": "Gemini 3.7 Flash",
                "provider_label": "Google",
                "description": "Hybrid reasoning and low-latency multimodal model",
            },
            ("gemini", "gemini-3.6-flash"): {
                "display_name": "Gemini 3.6 Flash",
                "provider_label": "Google",
                "description": "High-throughput, balanced multimodal reasoning model",
            },
            ("gemini", "gemini-3.5-flash-lite"): {
                "display_name": "Gemini 3.5 Flash-Lite",
                "provider_label": "Google",
                "description": "Ultra-lightweight low-latency model",
            },
            ("gemini", "gemini-flash-lite-latest"): {
                "display_name": "Gemini Flash-Lite Latest",
                "provider_label": "Google",
                "description": "Latest stable Gemini Flash-Lite channel",
            },
            ("gemini", "gemini-flash-latest"): {
                "display_name": "Gemini Flash Latest",
                "provider_label": "Google",
                "description": "Latest stable Gemini Flash channel",
            },
            ("gemini", "gemini-pro-latest"): {
                "display_name": "Gemini Pro Latest",
                "provider_label": "Google",
                "description": "Google's advanced reasoning and complex analysis model",
            },
            # Groq
            ("groq", "openai/gpt-oss-120b"): {
                "display_name": "GPT-OSS 120B",
                "provider_label": "Groq",
                "description": "Ultra-fast 120B reasoning model on Groq LPU",
            },
            ("groq", "openai/gpt-oss-20b"): {
                "display_name": "GPT-OSS 20B",
                "provider_label": "Groq",
                "description": "Sub-second lightweight conversational model",
            },
            ("groq", "qwen/qwen3.8-27b"): {
                "display_name": "Qwen 3.8 27B",
                "provider_label": "Groq",
                "description": "High-throughput multilingual model",
            },
            # OpenRouter
            ("openrouter", "nvidia/nemotron-3-ultra-550b-a55b:free"): {
                "display_name": "Gemini 3.8 Flash",
                "provider_label": "OpenRouter",
                "description": "Multimodal Flash hosted on OpenRouter",
            },
            ("openrouter", "google/gemini-2.5-flash"): {
                "display_name": "Gemini 2.5 Flash",
                "provider_label": "OpenRouter",
                "description": "Multimodal Flash hosted on OpenRouter",
            },
            ("openrouter", "nvidia/nemotron-3.5-lightning:free"): {
                "display_name": "Nemotron 3.5 Lightning",
                "provider_label": "OpenRouter",
                "description": "NVIDIA fast reasoning model (Free Tier)",
            },
            ("openrouter", "liquid/lfm-2.5-2.6b:free"): {
                "display_name": "Liquid LFM 2.6B",
                "provider_label": "OpenRouter",
                "description": "Liquid neural architecture (Free Tier)",
            },
            ("openrouter", "nvidia/nemotron-3-super-120b-a12b:free"): {
                "display_name": "Nemotron 3 Super 120B",
                "provider_label": "OpenRouter",
                "description": "NVIDIA 120B reasoning model (Free Tier)",
            },
        }

        def _is_configured(prov: str) -> bool:
            if prov == "gemini":
                return bool(getattr(settings, "PWANIMATE_GEMINI_API_KEY", "").strip())
            elif prov == "groq":
                return bool(getattr(settings, "PWANIMATE_GROQ_API_KEY", "").strip())
            elif prov == "openrouter":
                return bool(getattr(settings, "PWANIMATE_OPENROUTER_API_KEY", "").strip())
            elif prov == "mock":
                return True
            return False

        entries = []
        for item in fallback_chain:
            p = item.get("provider", "").strip().lower()
            m = item.get("model", "")
            is_limited = self.is_rate_limited(p, m)
            cooldown = self.get_cooldown_remaining(p, m)
            meta = MODEL_METADATA.get((p, m), {})
            entries.append({
                "provider": p,
                "model": m,
                "display_name": meta.get("display_name", m or p.title()),
                "provider_label": meta.get("provider_label", p.title()),
                "description": meta.get("description", ""),
                "is_configured": _is_configured(p),
                "rate_limited": is_limited,
                "cooldown_remaining": cooldown,
            })

        return {
            "cooldown_duration": self.cooldown,
            "models": entries,
        }

    def clear(self) -> None:
        """Clear all rate-limit records. Primarily for testing."""
        with self._lock:
            self._rate_limit_hits.clear()


# Module-level singleton
_tracker_instance: Optional[QuotaTracker] = None
_tracker_lock = threading.Lock()


def get_quota_tracker() -> QuotaTracker:
    """Return the global QuotaTracker singleton."""
    global _tracker_instance
    if _tracker_instance is None:
        with _tracker_lock:
            if _tracker_instance is None:
                _tracker_instance = QuotaTracker()
    return _tracker_instance
