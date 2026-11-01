"""
Unit tests for the SelfHostedEmbeddingProvider (design doc §5, §10 item 1).

Mirrors the style of test_multi_provider_embeddings.py: mocked HTTP, contract
tests against the spark-vector response shape.
"""

import os
import django
from unittest import TestCase
from unittest.mock import patch, MagicMock
import requests
from django.conf import settings
from django.test import override_settings

if not settings.configured:
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "pwaninet.settings.local")
    try:
        django.setup()
    except Exception:
        settings.configure(
            PWANIMATE_ENABLED=True,
            PWANIMATE_EMBEDDING_PROVIDER="gemini",
            PWANIMATE_EMBEDDING_MODEL="gemini-embedding-2",
            PWANIMATE_EMBEDDING_DIMENSIONS=768,
            PWANIMATE_EMBEDDING_BATCH_SIZE=32,
            PWANIMATE_SELFHOSTED_EMBEDDINGS_URL="http://spark_vector:8100/v1/embeddings",
            PWANIMATE_SELFHOSTED_EMBEDDINGS_MODEL="multilingual-e5-base",
            PWANIMATE_SELFHOSTED_EMBEDDINGS_TIMEOUT=10,
            PWANIMATE_SELFHOSTED_EMBEDDINGS_TOKEN="",
        )

from pwanimate.ai.embeddings.base import (
    EmbeddingAuthenticationError,
    EmbeddingConfigurationError,
    EmbeddingDimensionMismatchError,
    EmbeddingInvalidRequestError,
    EmbeddingMalformedResponseError,
    EmbeddingQuotaExhaustedError,
    EmbeddingServerResponseError,
    EmbeddingTimeoutError,
)
from pwanimate.ai.embeddings.factory import get_embedding_provider
from pwanimate.ai.embeddings.selfhosted import SelfHostedEmbeddingProvider

TEST_URL = "http://spark_vector:8100/v1/embeddings"
DIM = 768


def make_vector(dim=DIM, fill=0.5):
    return [fill] * dim


def ok_response(vectors, model="multilingual-e5-base"):
    response = MagicMock()
    response.status_code = 200
    response.json.return_value = {
        "object": "list",
        "model": model,
        "data": [
            {"object": "embedding", "index": idx, "embedding": vec}
            for idx, vec in enumerate(vectors)
        ],
        "usage": {"prompt_tokens": 12, "total_tokens": 12},
    }
    return response


def error_response(status, text="boom"):
    response = MagicMock()
    response.status_code = status
    response.text = text
    response.json.side_effect = ValueError("not json")
    return response


class SelfHostedConfigurationTests(TestCase):
    def test_missing_url_marks_provider_unconfigured(self):
        provider = SelfHostedEmbeddingProvider(url="")
        self.assertFalse(provider.is_configured())

    def test_embed_without_url_raises_configuration_error(self):
        provider = SelfHostedEmbeddingProvider(url="")
        with self.assertRaises(EmbeddingConfigurationError):
            provider.embed_texts(["hello"])

    def test_configured_defaults(self):
        provider = SelfHostedEmbeddingProvider(url=TEST_URL)
        self.assertTrue(provider.is_configured())
        # D5: get_model_name() is the DB stamp + retrieval filter.
        self.assertEqual(provider.get_model_name(), "multilingual-e5-base")
        self.assertEqual(provider.get_dimensions(), 768)

    @override_settings(
        PWANIMATE_SELFHOSTED_EMBEDDINGS_URL="http://custom:8100/v1/embeddings",
        PWANIMATE_SELFHOSTED_EMBEDDINGS_MODEL="multilingual-e5-base",
        PWANIMATE_SELFHOSTED_EMBEDDINGS_TIMEOUT=7,
    )
    def test_settings_plumbing(self):
        provider = SelfHostedEmbeddingProvider()
        self.assertEqual(provider.url, "http://custom:8100/v1/embeddings")
        self.assertEqual(provider.timeout, 7)
        self.assertTrue(provider.is_configured())


class SelfHostedHappyPathTests(TestCase):
    @patch("pwanimate.ai.embeddings.selfhosted.requests.post")
    def test_embed_texts_batch(self, mock_post):
        mock_post.return_value = ok_response([make_vector(), make_vector(fill=0.25)])
        provider = SelfHostedEmbeddingProvider(url=TEST_URL)

        vectors = provider.embed_texts(["doc one", "doc two"])

        self.assertEqual(len(vectors), 2)
        self.assertEqual(len(vectors[0]), DIM)
        payload = mock_post.call_args.kwargs["json"]
        self.assertEqual(payload["model"], "multilingual-e5-base")
        self.assertEqual(payload["input"], ["doc one", "doc two"])
        self.assertEqual(payload["task_type"], "RETRIEVAL_DOCUMENT")
        # No token configured -> no Authorization header.
        self.assertNotIn("Authorization", mock_post.call_args.kwargs["headers"])
        # No client-side retries (§5): exactly one POST.
        self.assertEqual(mock_post.call_count, 1)

    @patch("pwanimate.ai.embeddings.selfhosted.requests.post")
    def test_embed_query_uses_retrieval_query_task_type(self, mock_post):
        mock_post.return_value = ok_response([make_vector()])
        provider = SelfHostedEmbeddingProvider(url=TEST_URL)

        vector = provider.embed_query("when are fees due?")

        self.assertEqual(len(vector), DIM)
        self.assertEqual(mock_post.call_args.kwargs["json"]["task_type"], "RETRIEVAL_QUERY")

    @patch("pwanimate.ai.embeddings.selfhosted.requests.post")
    def test_empty_input_short_circuits(self, mock_post):
        provider = SelfHostedEmbeddingProvider(url=TEST_URL)
        self.assertEqual(provider.embed_texts([]), [])
        mock_post.assert_not_called()

    @patch("pwanimate.ai.embeddings.selfhosted.requests.post")
    def test_bearer_token_sent_when_configured(self, mock_post):
        mock_post.return_value = ok_response([make_vector()])
        provider = SelfHostedEmbeddingProvider(url=TEST_URL, token="s3cret")

        provider.embed_texts(["x"])

        self.assertEqual(
            mock_post.call_args.kwargs["headers"]["Authorization"], "Bearer s3cret"
        )

    @patch("pwanimate.ai.embeddings.selfhosted.requests.post")
    def test_unordered_indexes_are_reordered(self, mock_post):
        first, second = make_vector(fill=0.1), make_vector(fill=0.9)
        response = ok_response([first, second])
        response.json.return_value["data"] = [
            {"object": "embedding", "index": 1, "embedding": second},
            {"object": "embedding", "index": 0, "embedding": first},
        ]
        mock_post.return_value = response
        provider = SelfHostedEmbeddingProvider(url=TEST_URL)

        vectors = provider.embed_texts(["a", "b"])

        self.assertEqual(vectors[0], first)
        self.assertEqual(vectors[1], second)


class SelfHostedErrorMappingTests(TestCase):
    def _provider(self):
        return SelfHostedEmbeddingProvider(url=TEST_URL)

    @patch("pwanimate.ai.embeddings.selfhosted.requests.post")
    def test_timeout_maps_to_timeout_error(self, mock_post):
        mock_post.side_effect = requests.exceptions.Timeout("read timed out")
        with self.assertRaises(EmbeddingTimeoutError):
            self._provider().embed_texts(["x"])

    @patch("pwanimate.ai.embeddings.selfhosted.requests.post")
    def test_connection_error_maps_to_timeout_error(self, mock_post):
        mock_post.side_effect = requests.exceptions.ConnectionError("refused")
        with self.assertRaises(EmbeddingTimeoutError):
            self._provider().embed_texts(["x"])

    @patch("pwanimate.ai.embeddings.selfhosted.requests.post")
    def test_401_maps_to_authentication_error(self, mock_post):
        mock_post.return_value = error_response(401, "bad token")
        with self.assertRaises(EmbeddingAuthenticationError):
            self._provider().embed_texts(["x"])

    @patch("pwanimate.ai.embeddings.selfhosted.requests.post")
    def test_429_maps_to_quota_exhausted_error(self, mock_post):
        mock_post.return_value = error_response(429, "slow down")
        with self.assertRaises(EmbeddingQuotaExhaustedError):
            self._provider().embed_texts(["x"])

    @patch("pwanimate.ai.embeddings.selfhosted.requests.post")
    def test_400_maps_to_invalid_request_error(self, mock_post):
        mock_post.return_value = error_response(400, "bad payload")
        with self.assertRaises(EmbeddingInvalidRequestError) as ctx:
            self._provider().embed_texts(["x"])
        self.assertEqual(ctx.exception.status_code, 400)

    @patch("pwanimate.ai.embeddings.selfhosted.requests.post")
    def test_500_maps_to_server_response_error(self, mock_post):
        mock_post.return_value = error_response(500, "traceback")
        with self.assertRaises(EmbeddingServerResponseError):
            self._provider().embed_texts(["x"])

    @patch("pwanimate.ai.embeddings.selfhosted.requests.post")
    def test_dimension_mismatch_detected(self, mock_post):
        mock_post.return_value = ok_response([[0.5] * (DIM - 1)])
        with self.assertRaises(EmbeddingDimensionMismatchError):
            self._provider().embed_texts(["x"])

    @patch("pwanimate.ai.embeddings.selfhosted.requests.post")
    def test_malformed_json_detected(self, mock_post):
        response = MagicMock()
        response.status_code = 200
        response.text = "<html>not json</html>"
        response.json.side_effect = ValueError("nope")
        mock_post.return_value = response
        with self.assertRaises(EmbeddingMalformedResponseError):
            self._provider().embed_texts(["x"])

    @patch("pwanimate.ai.embeddings.selfhosted.requests.post")
    def test_embedding_count_mismatch_detected(self, mock_post):
        mock_post.return_value = ok_response([make_vector()])  # asked for 2, got 1
        with self.assertRaises(EmbeddingMalformedResponseError):
            self._provider().embed_texts(["a", "b"])


class SelfHostedFactoryTests(TestCase):
    def test_factory_resolves_selfhosted(self):
        provider = get_embedding_provider("selfhosted")
        self.assertIsInstance(provider, SelfHostedEmbeddingProvider)

    def test_factory_resolves_local_alias(self):
        provider = get_embedding_provider("local")
        self.assertIsInstance(provider, SelfHostedEmbeddingProvider)

    @override_settings(PWANIMATE_EMBEDDING_PROVIDER="selfhosted")
    def test_factory_honors_settings(self):
        provider = get_embedding_provider()
        self.assertIsInstance(provider, SelfHostedEmbeddingProvider)
