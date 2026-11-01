"""
What each model can do.

This used to be a literal in the middle of the router's chain resolution::

    supports_images = (
        p_name == "gemini" and model_id.startswith("gemini-")
    ) or (
        p_name == "groq" and model_id == "qwen/qwen3.8-27b" and image_count <= 3
    )

Which meant adding a vision provider was a code change in the hot path, and
forgetting it made the provider *silently* vanish from every image request.
A table makes it a data change.

The rules are deliberately conservative: a model that is not listed here is
treated as text-only. That is the safe direction to be wrong in -- the router
will fall back to a model that is known to handle the request rather than
handing an image to something that cannot.
"""

from dataclasses import dataclass
from typing import Optional

from django.conf import settings

# Provider-level defaults. A provider not named here handles no images.
DEFAULT_MAX_IMAGES = 10


@dataclass(frozen=True)
class ModelCapability:
    """What a model can accept."""

    images: bool = False
    max_images: Optional[int] = None

    def accepts_images(self, image_count: int) -> bool:
        """Whether a turn carrying ``image_count`` images can be served.

        A turn with no images needs no image capability, so a text-only
        model accepts it.
        """
        if image_count <= 0:
            return True
        if not self.images:
            return False
        if self.max_images is None:
            return True
        return image_count <= self.max_images


TEXT_ONLY = ModelCapability()


def _configured_vision_models() -> dict:
    """Vision models named in settings, as ``{provider: [model, ...]}``."""
    configured = getattr(settings, "PWANIMATE_VISION_CAPABLE_MODELS", None)
    if configured:
        return {str(k): [str(m) for m in v] for k, v in dict(configured).items()}
    return {
        "cloudflare": [
            str(getattr(settings, "PWANIMATE_CLOUDFLARE_VISION_MODEL", "") or ""),
        ],
    }


def model_capability(provider: str, model: str) -> ModelCapability:
    """
    The capability of one (provider, model) pair.

    An empty or missing model means "whatever this provider's default is",
    which is how the chain entries that only name a provider are handled.
    """
    provider = (provider or "").strip().lower()
    model = (model or "").strip()
    model_lower = model.lower()

    if provider == "gemini":
        if not model or model_lower.startswith("gemini-"):
            return ModelCapability(images=True, max_images=None)
        return TEXT_ONLY

    if provider == "groq":
        if model_lower == "qwen/qwen3.8-27b":
            return ModelCapability(images=True, max_images=3)
        return TEXT_ONLY

    if provider == "cloudflare":
        allowed = _configured_vision_models().get("cloudflare", [])
        if not model:
            return TEXT_ONLY
        if model in allowed:
            max_images = getattr(settings, "PWANIMATE_VISION_MAX_IMAGES", DEFAULT_MAX_IMAGES)
            return ModelCapability(images=True, max_images=int(max_images))
        # Any other Cloudflare model is assumed to be text-only.
        return TEXT_ONLY

    return TEXT_ONLY


def provider_supports_images(provider: str, model: str, image_count: int) -> bool:
    """Whether this (provider, model) can serve a turn carrying ``image_count`` images."""
    return model_capability(provider, model).accepts_images(image_count)
