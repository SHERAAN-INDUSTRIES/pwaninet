"""
Google Gemini embedding provider using direct REST API.
Model: gemini-embedding-2 (768 dimensions).
"""

import logging
from typing import List, Optional
import requests
from django.conf import settings

from .base import (
    BaseEmbeddingProvider,
    EmbeddingProviderError,
    EmbeddingConfigurationError,
    EmbeddingDimensionMismatchError,
    EmbeddingQuotaExhaustedError,
)

logger = logging.getLogger(__name__)

GEMINI_API_BASE_URL = "https://generativelanguage.googleapis.com/v1beta"


class GeminiEmbeddingProvider(BaseEmbeddingProvider):
    """
    Google Gemini vector embedding provider.
    Connects to the official Google Generative Language REST API.
    Defaults to gemini-embedding-2 with 768 dimensions.
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        model_name: Optional[str] = None,
        dimensions: int = 768,
        timeout: int = 10
    ):
        self.api_key = api_key if api_key is not None else getattr(settings, 'PWANIMATE_GEMINI_API_KEY', '')
        # PWANIMATE_EMBEDDING_MODEL names the ACTIVE provider's model. Only
        # adopt it when it really is a Gemini model id — otherwise this
        # provider would stamp its vectors with another model's name (D5:
        # the stamp must match the model that produced the vector).
        configured_model = (getattr(settings, 'PWANIMATE_EMBEDDING_MODEL', '') or '').strip()
        if configured_model and (
            configured_model.startswith('gemini-') or configured_model.startswith('models/gemini')
        ):
            default_model = configured_model
        else:
            default_model = 'gemini-embedding-2'
        self.model_name = model_name or default_model
        self.dimensions = dimensions or getattr(settings, 'PWANIMATE_EMBEDDING_DIMENSIONS', 768)
        self.timeout = timeout

    def is_configured(self) -> bool:
        return bool(self.api_key and self.api_key.strip())

    def get_dimensions(self) -> int:
        return self.dimensions

    def get_model_name(self) -> str:
        return self.model_name

    def embed_texts(self, texts: List[str], task_type: str = "RETRIEVAL_DOCUMENT") -> List[List[float]]:
        if not texts:
            return []

        if not self.is_configured():
            raise EmbeddingConfigurationError(
                "Gemini API key is not configured. Set PWANIMATE_GEMINI_API_KEY in environment or settings."
            )

        # Gemini model resource name
        model_resource = f"models/{self.model_name}" if not self.model_name.startswith("models/") else self.model_name
        endpoint = f"{GEMINI_API_BASE_URL}/{model_resource}:batchEmbedContents"

        requests_payload = []
        for text in texts:
            cleaned = text.strip() or " "
            requests_payload.append({
                "model": model_resource,
                "content": {
                    "parts": [{"text": cleaned}]
                },
                "taskType": task_type,
                "outputDimensionality": self.dimensions
            })

        body = {"requests": requests_payload}
        headers = {
            "Content-Type": "application/json",
            "x-goog-api-key": self.api_key,
        }

        try:
            response = requests.post(
                endpoint,
                json=body,
                headers=headers,
                timeout=self.timeout
            )
        except requests.exceptions.Timeout as e:
            raise EmbeddingProviderError(f"Gemini embedding request timed out after {self.timeout}s: {e}") from e
        except requests.exceptions.RequestException as e:
            raise EmbeddingProviderError(f"Gemini embedding network connection error: {e}") from e

        if response.status_code == 429:
            retry_after = response.headers.get('Retry-After', 'not provided')
            err_msg = (
                f"Gemini quota exhausted (429). Retry-After: {retry_after}. "
                f"Body: {response.text[:200]}"
            )
            logger.warning(err_msg)
            raise EmbeddingQuotaExhaustedError(err_msg)

        if response.status_code != 200:
            err_msg = f"Gemini API error (status {response.status_code}): {response.text[:300]}"
            logger.error(err_msg)
            raise EmbeddingProviderError(err_msg)

        data = response.json()
        raw_embeddings = data.get("embeddings", [])

        if len(raw_embeddings) != len(texts):
            raise EmbeddingProviderError(
                f"Gemini returned {len(raw_embeddings)} embeddings for {len(texts)} inputs."
            )

        vectors: List[List[float]] = []
        for idx, item in enumerate(raw_embeddings):
            vec = item.get("values", [])
            if len(vec) != self.dimensions:
                raise EmbeddingDimensionMismatchError(
                    f"Gemini vector index {idx} has {len(vec)} dimensions, expected {self.dimensions}."
                )
            vectors.append(vec)

        return vectors

    def embed_query(self, text: str) -> List[float]:
        results = self.embed_texts([text], task_type="RETRIEVAL_QUERY")
        return results[0]
