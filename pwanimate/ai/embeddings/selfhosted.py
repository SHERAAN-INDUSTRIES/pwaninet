"""
Self-hosted spark-vector embedding provider (design doc §5).

Talks to the local spark-vector FastAPI/ONNX worker over the internal Docker
network with an OpenAI-compatible payload plus a task_type extension. No API
key concept; an optional shared bearer token is supported.
"""

import logging
from typing import List, Optional

import requests
from django.conf import settings

from .base import (
    BaseEmbeddingProvider,
    EmbeddingAuthenticationError,
    EmbeddingConfigurationError,
    EmbeddingDimensionMismatchError,
    EmbeddingInvalidRequestError,
    EmbeddingMalformedResponseError,
    EmbeddingQuotaExhaustedError,
    EmbeddingServerResponseError,
    EmbeddingTimeoutError,
)

logger = logging.getLogger(__name__)

DEFAULT_URL = "http://spark_vector:8100/v1/embeddings"
DEFAULT_MODEL = "multilingual-e5-base"


class SelfHostedEmbeddingProvider(BaseEmbeddingProvider):
    """
    Local spark-vector worker client.

    Behavior contract (design doc §5):
    - is_configured() -> base URL present (no API key concept)
    - embed_query() -> task_type=RETRIEVAL_QUERY; embed_texts() passes task_type through
    - get_model_name() -> the configured model name (DB stamp + retrieval filter, D5)
    - get_dimensions() -> 768
    - no client-side retries (timeout budget is the UX; Celery has task retries)
    """

    def __init__(
        self,
        url: Optional[str] = None,
        model_name: Optional[str] = None,
        dimensions: Optional[int] = None,
        timeout: Optional[int] = None,
        token: Optional[str] = None,
        batch_size: Optional[int] = None,
    ):
        if url is None:
            url = getattr(settings, 'PWANIMATE_SELFHOSTED_EMBEDDINGS_URL', DEFAULT_URL)
        if model_name is None:
            model_name = getattr(settings, 'PWANIMATE_SELFHOSTED_EMBEDDINGS_MODEL', DEFAULT_MODEL)
        if dimensions is None:
            dimensions = getattr(settings, 'PWANIMATE_EMBEDDING_DIMENSIONS', 768)
        if timeout is None:
            timeout = getattr(settings, 'PWANIMATE_SELFHOSTED_EMBEDDINGS_TIMEOUT', 10)
        if token is None:
            token = getattr(settings, 'PWANIMATE_SELFHOSTED_EMBEDDINGS_TOKEN', '')
        if batch_size is None:
            batch_size = getattr(settings, 'PWANIMATE_SELFHOSTED_EMBEDDING_BATCH_SIZE', 4)

        self.url = (url or '').strip()
        self.model_name = (model_name or DEFAULT_MODEL).strip()
        self.dimensions = int(dimensions)
        self.timeout = timeout
        self.token = (token or '').strip()
        # Requests are split into sub-batches: one big request would hold the
        # worker's single inference slot (and blow the per-request token
        # budget), starving interactive queries and tripping the timeout.
        self.batch_size = max(1, int(batch_size))

    def is_configured(self) -> bool:
        return bool(self.url and self.url.strip())

    def get_dimensions(self) -> int:
        return self.dimensions

    def get_model_name(self) -> str:
        # This string is the DB stamp AND the retrieval filter (D5): renaming
        # it triggers a corpus-wide re-embed — change only as a version bump.
        return self.model_name

    def embed_texts(self, texts: List[str], task_type: str = "RETRIEVAL_DOCUMENT") -> List[List[float]]:
        if not texts:
            return []

        if not self.is_configured():
            raise EmbeddingConfigurationError(
                "spark-vector URL is not configured. Set PWANIMATE_SELFHOSTED_EMBEDDINGS_URL "
                "in the environment or settings."
            )

        vectors: List[List[float]] = []
        for start in range(0, len(texts), self.batch_size):
            vectors.extend(
                self._embed_batch(texts[start:start + self.batch_size], task_type)
            )
        return vectors

    def _embed_batch(self, texts: List[str], task_type: str) -> List[List[float]]:
        """One HTTP round trip to spark-vector for a single sub-batch."""
        headers = {"Content-Type": "application/json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"

        payload = {
            "model": self.model_name,
            "input": list(texts),
            "task_type": task_type,
        }

        try:
            response = requests.post(self.url, json=payload, headers=headers, timeout=self.timeout)
        except requests.exceptions.Timeout as exc:
            raise EmbeddingTimeoutError(
                f"spark-vector embedding request timed out after {self.timeout}s: {exc}"
            ) from exc
        except requests.exceptions.RequestException as exc:
            # Spec §5 maps connect/read failures onto EmbeddingTimeoutError;
            # the retrieval layer degrades to lexical search either way (§7).
            raise EmbeddingTimeoutError(f"spark-vector network connection error: {exc}") from exc

        if response.status_code in (401, 403):
            raise EmbeddingAuthenticationError(
                f"spark-vector rejected credentials (status {response.status_code}).",
                status_code=response.status_code,
            )
        if response.status_code == 429:
            # Should not occur locally — kept for taxonomy completeness (§5).
            raise EmbeddingQuotaExhaustedError(
                f"spark-vector rate limited (429). Body: {response.text[:200]}",
                status_code=429,
            )
        if 400 <= response.status_code < 500:
            raise EmbeddingInvalidRequestError(
                f"spark-vector rejected request (status {response.status_code}): {response.text[:300]}",
                status_code=response.status_code,
            )
        if response.status_code >= 500:
            raise EmbeddingServerResponseError(
                f"spark-vector server error (status {response.status_code}): {response.text[:300]}",
                status_code=response.status_code,
            )

        try:
            data = response.json()
        except ValueError as exc:
            raise EmbeddingMalformedResponseError(
                f"spark-vector returned a non-JSON body: {response.text[:200]}"
            ) from exc

        raw = data.get("data") if isinstance(data, dict) else None
        if not isinstance(raw, list) or len(raw) != len(texts):
            count = len(raw) if isinstance(raw, list) else "invalid"
            raise EmbeddingMalformedResponseError(
                f"spark-vector returned {count} embeddings for {len(texts)} inputs."
            )

        # Preserve index order (OpenAI-style payloads may arrive unordered).
        if all(isinstance(item, dict) and "index" in item for item in raw):
            ordered = [
                item
                for _, item in sorted(((int(item["index"]), item) for item in raw), key=lambda pair: pair[0])
            ]
        else:
            ordered = raw

        vectors: List[List[float]] = []
        for idx, item in enumerate(ordered):
            vector = item.get("embedding") if isinstance(item, dict) else None
            if not isinstance(vector, list) or len(vector) != self.dimensions:
                size = len(vector) if isinstance(vector, list) else "invalid"
                raise EmbeddingDimensionMismatchError(
                    f"spark-vector vector index {idx} has {size} dimensions, expected {self.dimensions}."
                )
            vectors.append(vector)

        return vectors

    def embed_query(self, text: str) -> List[float]:
        results = self.embed_texts([text], task_type="RETRIEVAL_QUERY")
        return results[0]
