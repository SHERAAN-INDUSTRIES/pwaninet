"""
Embedding Provider Factory for Pwanimate.
Resolves and instantiates the active embedding provider based on configuration.
"""

from typing import Optional
from django.conf import settings
from .base import BaseEmbeddingProvider
from .gemini import GeminiEmbeddingProvider
from .mock import MockEmbeddingProvider
from .jina import JinaEmbeddingProvider
from .voyage import VoyageEmbeddingProvider
from .cloudflare import CloudflareEmbeddingProvider
from .selfhosted import SelfHostedEmbeddingProvider


def get_embedding_provider(
    provider_name: Optional[str] = None,
    **kwargs
) -> BaseEmbeddingProvider:
    """
    Factory function returning an instance of the configured embedding provider.

    Args:
        provider_name: Optional override for provider type ('gemini', 'mock', 'jina', 'voyage', 'cloudflare', 'selfhosted').
        **kwargs: Optional constructor arguments passed to the provider.

    Returns:
        BaseEmbeddingProvider instance.
    """
    name = (provider_name or getattr(settings, 'PWANIMATE_EMBEDDING_PROVIDER', 'gemini')).lower().strip()

    if name == 'gemini':
        return GeminiEmbeddingProvider(**kwargs)
    elif name == 'mock':
        return MockEmbeddingProvider(**kwargs)
    elif name == 'jina':
        return JinaEmbeddingProvider(**kwargs)
    elif name == 'voyage':
        return VoyageEmbeddingProvider(**kwargs)
    elif name in ('cloudflare', 'cf'):
        return CloudflareEmbeddingProvider(**kwargs)
    elif name in ('selfhosted', 'local'):
        return SelfHostedEmbeddingProvider(**kwargs)
    else:
        raise ValueError(
            f"Unsupported embedding provider: '{name}'. Supported providers are: 'gemini', 'mock', 'jina', 'voyage', 'cloudflare', 'selfhosted'."
        )
