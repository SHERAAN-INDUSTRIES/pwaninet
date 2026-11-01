"""
Unit tests for Pwanimate Multi-Provider Embedding Subsystem:
- Jina AI (JinaEmbeddingProvider)
- Voyage AI (VoyageEmbeddingProvider)
- Cloudflare Workers AI (CloudflareEmbeddingProvider)
- Factory Resolution & Error Hierarchy
"""

import os
import django
from unittest import TestCase
from unittest.mock import patch, MagicMock
import requests
from django.conf import settings

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
            PWANIMATE_GEMINI_API_KEY="",
            PWANIMATE_JINA_API_KEY="",
            PWANIMATE_JINA_MODEL="jina-embeddings-v3",
            PWANIMATE_VOYAGE_API_KEY="",
            PWANIMATE_VOYAGE_MODEL="voyage-3",
            PWANIMATE_CLOUDFLARE_API_TOKEN="",
            PWANIMATE_CLOUDFLARE_ACCOUNT_ID="",
            PWANIMATE_CLOUDFLARE_MODEL="@cf/baai/bge-base-en-v1.5",
        )

from pwanimate.ai.embeddings.base import (
    EmbeddingProviderError,
    EmbeddingConfigurationError,
    EmbeddingAuthenticationError,
    EmbeddingDimensionMismatchError,
    EmbeddingQuotaExhaustedError,
    EmbeddingTimeoutError,
    EmbeddingInvalidRequestError,
    EmbeddingServerResponseError,
    EmbeddingMalformedResponseError,
)
from pwanimate.ai.embeddings.gemini import GeminiEmbeddingProvider
from pwanimate.ai.embeddings.mock import MockEmbeddingProvider
from pwanimate.ai.embeddings.selfhosted import SelfHostedEmbeddingProvider
from pwanimate.ai.embeddings.jina import JinaEmbeddingProvider
from pwanimate.ai.embeddings.voyage import VoyageEmbeddingProvider
from pwanimate.ai.embeddings.cloudflare import CloudflareEmbeddingProvider
from pwanimate.ai.embeddings.factory import get_embedding_provider


class JinaEmbeddingProviderTestCase(TestCase):
    """Test suite for Jina AI embedding provider."""

    def test_unconfigured_provider_raises_configuration_error(self):
        provider = JinaEmbeddingProvider(api_key="")
        self.assertFalse(provider.is_configured())
        with self.assertRaises(EmbeddingConfigurationError) as ctx:
            provider.embed_texts(["Sample text"])
        self.assertIn("Jina AI API key is not configured", str(ctx.exception))
        self.assertEqual(ctx.exception.provider, "jina")

    @patch("requests.post")
    def test_successful_batch_embed(self, mock_post):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "model": "jina-embeddings-v3",
            "data": [
                {"index": 0, "embedding": [0.05] * 768},
                {"index": 1, "embedding": [-0.03] * 768},
            ]
        }
        mock_post.return_value = mock_response

        provider = JinaEmbeddingProvider(api_key="jina-test-token", dimensions=768)
        self.assertTrue(provider.is_configured())
        self.assertEqual(provider.get_model_name(), "jina-embeddings-v3")
        self.assertEqual(provider.get_dimensions(), 768)

        vectors = provider.embed_texts(["First text", "Second text"], task_type="RETRIEVAL_DOCUMENT")
        self.assertEqual(len(vectors), 2)
        self.assertEqual(len(vectors[0]), 768)
        self.assertEqual(len(vectors[1]), 768)

        # Inspect request
        endpoint = mock_post.call_args[0][0]
        self.assertEqual(endpoint, "https://api.jina.ai/v1/embeddings")
        headers = mock_post.call_args[1]["headers"]
        self.assertEqual(headers["Authorization"], "Bearer jina-test-token")
        payload = mock_post.call_args[1]["json"]
        self.assertEqual(payload["model"], "jina-embeddings-v3")
        self.assertEqual(payload["task"], "retrieval.passage")
        self.assertEqual(payload["dimensions"], 768)
        self.assertTrue(payload["normalized"])
        self.assertEqual(payload["input"], ["First text", "Second text"])

    @patch("requests.post")
    def test_query_embed_uses_query_task(self, mock_post):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "data": [{"index": 0, "embedding": [0.1] * 768}]
        }
        mock_post.return_value = mock_response

        provider = JinaEmbeddingProvider(api_key="test-key", dimensions=768)
        vec = provider.embed_query("What is virtual memory?")
        self.assertEqual(len(vec), 768)

        payload = mock_post.call_args[1]["json"]
        self.assertEqual(payload["task"], "retrieval.query")

    @patch("requests.post")
    def test_dimension_mismatch_raises_error(self, mock_post):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "data": [{"index": 0, "embedding": [0.1] * 512}]  # 512 != expected 768
        }
        mock_post.return_value = mock_response

        provider = JinaEmbeddingProvider(api_key="test-key", dimensions=768)
        with self.assertRaises(EmbeddingDimensionMismatchError) as ctx:
            provider.embed_texts(["Chunk"])
        self.assertEqual(ctx.exception.provider, "jina")
        self.assertEqual(ctx.exception.details["actual"], 512)
        self.assertEqual(ctx.exception.details["expected"], 768)

    @patch("requests.post")
    def test_authentication_error(self, mock_post):
        mock_response = MagicMock()
        mock_response.status_code = 401
        mock_response.text = '{"detail": "Invalid API key"}'
        mock_post.return_value = mock_response

        provider = JinaEmbeddingProvider(api_key="bad-key")
        with self.assertRaises(EmbeddingAuthenticationError) as ctx:
            provider.embed_texts(["Chunk"])
        self.assertEqual(ctx.exception.status_code, 401)
        self.assertNotIn("bad-key", str(ctx.exception))

    @patch("requests.post")
    def test_rate_limit_quota_error(self, mock_post):
        mock_response = MagicMock()
        mock_response.status_code = 429
        mock_response.headers = {"Retry-After": "45"}
        mock_response.text = '{"detail": "Rate limit reached"}'
        mock_post.return_value = mock_response

        provider = JinaEmbeddingProvider(api_key="test-key")
        with self.assertRaises(EmbeddingQuotaExhaustedError) as ctx:
            provider.embed_texts(["Chunk"])
        self.assertEqual(ctx.exception.status_code, 429)
        self.assertEqual(ctx.exception.details.get("retry_after"), "45")

    @patch("requests.post")
    def test_timeout_error(self, mock_post):
        mock_post.side_effect = requests.exceptions.Timeout("Connection timed out")
        provider = JinaEmbeddingProvider(api_key="test-key")
        with self.assertRaises(EmbeddingTimeoutError) as ctx:
            provider.embed_texts(["Chunk"])
        self.assertEqual(ctx.exception.provider, "jina")

    @patch("requests.post")
    def test_malformed_response_error(self, mock_post):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"unexpected": "no_data_field"}
        mock_post.return_value = mock_response

        provider = JinaEmbeddingProvider(api_key="test-key")
        with self.assertRaises(EmbeddingMalformedResponseError):
            provider.embed_texts(["Chunk"])


class VoyageEmbeddingProviderTestCase(TestCase):
    """Test suite for Voyage AI embedding provider."""

    def test_unconfigured_provider_raises_configuration_error(self):
        provider = VoyageEmbeddingProvider(api_key="")
        self.assertFalse(provider.is_configured())
        with self.assertRaises(EmbeddingConfigurationError) as ctx:
            provider.embed_texts(["Sample text"])
        self.assertIn("Voyage AI API key is not configured", str(ctx.exception))
        self.assertEqual(ctx.exception.provider, "voyage")

    @patch("requests.post")
    def test_successful_batch_embed(self, mock_post):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "object": "list",
            "model": "voyage-3",
            "data": [
                {"index": 0, "embedding": [0.02] * 1024},
                {"index": 1, "embedding": [-0.04] * 1024},
            ]
        }
        mock_post.return_value = mock_response

        provider = VoyageEmbeddingProvider(api_key="voyage-test-token", dimensions=1024)
        self.assertTrue(provider.is_configured())
        self.assertEqual(provider.get_model_name(), "voyage-3")
        self.assertEqual(provider.get_dimensions(), 1024)

        vectors = provider.embed_texts(["Doc 1", "Doc 2"], task_type="RETRIEVAL_DOCUMENT")
        self.assertEqual(len(vectors), 2)
        self.assertEqual(len(vectors[0]), 1024)
        self.assertEqual(len(vectors[1]), 1024)

        # Inspect request
        endpoint = mock_post.call_args[0][0]
        self.assertEqual(endpoint, "https://api.voyageai.com/v1/embeddings")
        headers = mock_post.call_args[1]["headers"]
        self.assertEqual(headers["Authorization"], "Bearer voyage-test-token")
        payload = mock_post.call_args[1]["json"]
        self.assertEqual(payload["model"], "voyage-3")
        self.assertEqual(payload["input_type"], "document")
        self.assertEqual(payload["output_dimension"], 1024)
        self.assertEqual(payload["input"], ["Doc 1", "Doc 2"])

    @patch("requests.post")
    def test_query_embed_uses_query_input_type(self, mock_post):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "data": [{"index": 0, "embedding": [0.1] * 1024}]
        }
        mock_post.return_value = mock_response

        provider = VoyageEmbeddingProvider(api_key="test-key", dimensions=1024)
        vec = provider.embed_query("Kernel architecture")
        self.assertEqual(len(vec), 1024)

        payload = mock_post.call_args[1]["json"]
        self.assertEqual(payload["input_type"], "query")

    @patch("requests.post")
    def test_dimension_mismatch_raises_error(self, mock_post):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "data": [{"index": 0, "embedding": [0.1] * 512}]  # 512 != expected 1024
        }
        mock_post.return_value = mock_response

        provider = VoyageEmbeddingProvider(api_key="test-key", dimensions=1024)
        with self.assertRaises(EmbeddingDimensionMismatchError) as ctx:
            provider.embed_texts(["Doc"])
        self.assertEqual(ctx.exception.provider, "voyage")

    @patch("requests.post")
    def test_authentication_error(self, mock_post):
        mock_response = MagicMock()
        mock_response.status_code = 401
        mock_response.text = '{"detail": "Invalid API Key"}'
        mock_post.return_value = mock_response

        provider = VoyageEmbeddingProvider(api_key="bad-key")
        with self.assertRaises(EmbeddingAuthenticationError) as ctx:
            provider.embed_texts(["Doc"])
        self.assertEqual(ctx.exception.status_code, 401)
        self.assertNotIn("bad-key", str(ctx.exception))

    @patch("requests.post")
    def test_rate_limit_quota_error(self, mock_post):
        mock_response = MagicMock()
        mock_response.status_code = 429
        mock_response.headers = {"Retry-After": "30"}
        mock_response.text = '{"detail": "Rate limit exceeded"}'
        mock_post.return_value = mock_response

        provider = VoyageEmbeddingProvider(api_key="test-key")
        with self.assertRaises(EmbeddingQuotaExhaustedError) as ctx:
            provider.embed_texts(["Doc"])
        self.assertEqual(ctx.exception.status_code, 429)

    @patch("requests.post")
    def test_timeout_error(self, mock_post):
        mock_post.side_effect = requests.exceptions.Timeout("Read timeout")
        provider = VoyageEmbeddingProvider(api_key="test-key")
        with self.assertRaises(EmbeddingTimeoutError):
            provider.embed_texts(["Doc"])

    @patch("requests.post")
    def test_server_error(self, mock_post):
        mock_response = MagicMock()
        mock_response.status_code = 503
        mock_response.text = "Service Unavailable"
        mock_post.return_value = mock_response

        provider = VoyageEmbeddingProvider(api_key="test-key")
        with self.assertRaises(EmbeddingServerResponseError):
            provider.embed_texts(["Doc"])


class CloudflareEmbeddingProviderTestCase(TestCase):
    """Test suite for Cloudflare Workers AI embedding provider."""

    def test_unconfigured_provider_missing_both(self):
        provider = CloudflareEmbeddingProvider(api_token="", account_id="")
        self.assertFalse(provider.is_configured())
        with self.assertRaises(EmbeddingConfigurationError) as ctx:
            provider.embed_texts(["Sample text"])
        self.assertIn("PWANIMATE_CLOUDFLARE_API_TOKEN", str(ctx.exception))
        self.assertIn("PWANIMATE_CLOUDFLARE_ACCOUNT_ID", str(ctx.exception))

    def test_unconfigured_provider_missing_account_id(self):
        provider = CloudflareEmbeddingProvider(api_token="valid-token", account_id="")
        self.assertFalse(provider.is_configured())
        with self.assertRaises(EmbeddingConfigurationError) as ctx:
            provider.embed_texts(["Sample text"])
        self.assertIn("PWANIMATE_CLOUDFLARE_ACCOUNT_ID", str(ctx.exception))

    @patch("requests.post")
    def test_successful_batch_embed(self, mock_post):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "result": {
                "shape": [2, 768],
                "data": [
                    [0.03] * 768,
                    [-0.01] * 768,
                ]
            },
            "success": True,
            "errors": [],
            "messages": []
        }
        mock_post.return_value = mock_response

        provider = CloudflareEmbeddingProvider(
            api_token="cf-token-123",
            account_id="cf-account-abc",
            model_name="@cf/baai/bge-base-en-v1.5",
            dimensions=768
        )
        self.assertTrue(provider.is_configured())
        self.assertEqual(provider.get_model_name(), "@cf/baai/bge-base-en-v1.5")
        self.assertEqual(provider.get_dimensions(), 768)

        vectors = provider.embed_texts(["Text A", "Text B"])
        self.assertEqual(len(vectors), 2)
        self.assertEqual(len(vectors[0]), 768)
        self.assertEqual(len(vectors[1]), 768)

        # Inspect endpoint and headers
        endpoint = mock_post.call_args[0][0]
        expected_url = "https://api.cloudflare.com/client/v4/accounts/cf-account-abc/ai/run/@cf/baai/bge-base-en-v1.5"
        self.assertEqual(endpoint, expected_url)
        headers = mock_post.call_args[1]["headers"]
        self.assertEqual(headers["Authorization"], "Bearer cf-token-123")
        payload = mock_post.call_args[1]["json"]
        self.assertEqual(payload, {"text": ["Text A", "Text B"]})

    @patch("requests.post")
    def test_query_embed(self, mock_post):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "result": {
                "shape": [1, 768],
                "data": [[0.05] * 768]
            },
            "success": True,
            "errors": []
        }
        mock_post.return_value = mock_response

        provider = CloudflareEmbeddingProvider(
            api_token="token",
            account_id="acc",
            dimensions=768
        )
        vec = provider.embed_query("Search query")
        self.assertEqual(len(vec), 768)

    @patch("requests.post")
    def test_dimension_mismatch_raises_error(self, mock_post):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "result": {
                "shape": [1, 384],
                "data": [[0.05] * 384]  # 384 != 768
            },
            "success": True,
            "errors": []
        }
        mock_post.return_value = mock_response

        provider = CloudflareEmbeddingProvider(
            api_token="token",
            account_id="acc",
            dimensions=768
        )
        with self.assertRaises(EmbeddingDimensionMismatchError) as ctx:
            provider.embed_texts(["Text"])
        self.assertEqual(ctx.exception.provider, "cloudflare")

    @patch("requests.post")
    def test_authentication_error(self, mock_post):
        mock_response = MagicMock()
        mock_response.status_code = 403
        mock_response.text = '{"success": false, "errors": [{"code": 10000, "message": "Authentication error"}]}'
        mock_post.return_value = mock_response

        provider = CloudflareEmbeddingProvider(api_token="bad-token", account_id="acc")
        with self.assertRaises(EmbeddingAuthenticationError) as ctx:
            provider.embed_texts(["Text"])
        self.assertEqual(ctx.exception.status_code, 403)
        self.assertNotIn("bad-token", str(ctx.exception))

    @patch("requests.post")
    def test_rate_limit_quota_error(self, mock_post):
        mock_response = MagicMock()
        mock_response.status_code = 429
        mock_response.headers = {"Retry-After": "60"}
        mock_response.text = '{"success": false, "errors": [{"code": 10014, "message": "Rate limit exceeded"}]}'
        mock_post.return_value = mock_response

        provider = CloudflareEmbeddingProvider(api_token="token", account_id="acc")
        with self.assertRaises(EmbeddingQuotaExhaustedError) as ctx:
            provider.embed_texts(["Text"])
        self.assertEqual(ctx.exception.status_code, 429)

    @patch("requests.post")
    def test_success_false_in_json(self, mock_post):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "success": False,
            "errors": [{"code": 1000, "message": "Internal worker error"}],
            "result": None
        }
        mock_post.return_value = mock_response

        provider = CloudflareEmbeddingProvider(api_token="token", account_id="acc")
        with self.assertRaises(EmbeddingServerResponseError) as ctx:
            provider.embed_texts(["Text"])
        self.assertIn("Internal worker error", str(ctx.exception))

    @patch("requests.post")
    def test_timeout_error(self, mock_post):
        mock_post.side_effect = requests.exceptions.Timeout("Gateway timeout")
        provider = CloudflareEmbeddingProvider(api_token="token", account_id="acc")
        with self.assertRaises(EmbeddingTimeoutError):
            provider.embed_texts(["Text"])


class MultiProviderFactoryTestCase(TestCase):
    """Test suite for get_embedding_provider factory resolution."""

    def test_resolves_all_supported_providers(self):
        gemini = get_embedding_provider("gemini", api_key="test-key")
        self.assertIsInstance(gemini, GeminiEmbeddingProvider)

        mock_p = get_embedding_provider("mock")
        self.assertIsInstance(mock_p, MockEmbeddingProvider)

        jina = get_embedding_provider("jina", api_key="jina-key")
        self.assertIsInstance(jina, JinaEmbeddingProvider)

        voyage = get_embedding_provider("voyage", api_key="voyage-key")
        self.assertIsInstance(voyage, VoyageEmbeddingProvider)

        cf = get_embedding_provider("cloudflare", api_token="cf-token", account_id="cf-acc")
        self.assertIsInstance(cf, CloudflareEmbeddingProvider)

        cf_alias = get_embedding_provider("cf", api_token="cf-token", account_id="cf-acc")
        self.assertIsInstance(cf_alias, CloudflareEmbeddingProvider)

    def test_unsupported_provider_raises_value_error(self):
        with self.assertRaises(ValueError) as ctx:
            get_embedding_provider("unknown-provider")
        self.assertIn("Unsupported embedding provider", str(ctx.exception))

    def test_default_provider_follows_settings(self):
        """The default provider follows configuration (it became `selfhosted`
        at the local-embedding cutover); Gemini stays available explicitly."""
        configured = (getattr(settings, 'PWANIMATE_EMBEDDING_PROVIDER', 'gemini') or 'gemini').strip().lower()
        expected = {
            'gemini': GeminiEmbeddingProvider,
            'selfhosted': SelfHostedEmbeddingProvider,
            'local': SelfHostedEmbeddingProvider,
            'mock': MockEmbeddingProvider,
        }.get(configured)
        default_provider = get_embedding_provider()
        if expected is not None:
            self.assertIsInstance(default_provider, expected)
        # The Gemini provider is always reachable by name, whatever the default is.
        self.assertIsInstance(get_embedding_provider('gemini'), GeminiEmbeddingProvider)
        # ...and it never claims another provider's model id (D5: the stamp must
        # match the model that produced the vector).
        self.assertTrue(get_embedding_provider('gemini').get_model_name().startswith('gemini'))


class GeminiAndMockCompatibilityTestCase(TestCase):
    """Verify existing Gemini and Mock embedding providers continue operating unchanged."""

    def test_mock_provider_unchanged(self):
        provider = MockEmbeddingProvider(dimensions=768)
        self.assertTrue(provider.is_configured())
        self.assertEqual(provider.get_dimensions(), 768)
        self.assertEqual(provider.get_model_name(), "mock-embedding-768")

        texts = ["Sample chunk A", "Sample chunk B"]
        vectors = provider.embed_texts(texts)
        self.assertEqual(len(vectors), 2)
        self.assertEqual(len(vectors[0]), 768)

        # Determinism
        self.assertEqual(vectors[0], provider.embed_texts([texts[0]])[0])

    def test_gemini_provider_unconfigured(self):
        provider = GeminiEmbeddingProvider(api_key="")
        self.assertFalse(provider.is_configured())
        with self.assertRaises(EmbeddingConfigurationError):
            provider.embed_texts(["Chunk"])

    @patch("requests.post")
    def test_gemini_provider_success(self, mock_post):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "embeddings": [
                {"values": [0.02] * 768},
                {"values": [-0.01] * 768}
            ]
        }
        mock_post.return_value = mock_response

        provider = GeminiEmbeddingProvider(api_key="gemini-key-123", dimensions=768)
        self.assertTrue(provider.is_configured())
        self.assertEqual(provider.get_model_name(), "gemini-embedding-2")

        vectors = provider.embed_texts(["Doc 1", "Doc 2"])
        self.assertEqual(len(vectors), 2)
        self.assertEqual(len(vectors[0]), 768)

        # Confirm Gemini API conventions
        url = mock_post.call_args[0][0]
        self.assertIn("models/gemini-embedding-2:batchEmbedContents", url)
        self.assertEqual(mock_post.call_args[1]["headers"]["x-goog-api-key"], "gemini-key-123")
        self.assertEqual(mock_post.call_args[1]["json"]["requests"][0]["outputDimensionality"], 768)

    @patch("requests.post")
    def test_gemini_provider_dimension_mismatch(self, mock_post):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "embeddings": [{"values": [0.01] * 512}]
        }
        mock_post.return_value = mock_response

        provider = GeminiEmbeddingProvider(api_key="key", dimensions=768)
        with self.assertRaises(EmbeddingDimensionMismatchError):
            provider.embed_texts(["Doc"])

    @patch("requests.post")
    def test_gemini_provider_quota_error(self, mock_post):
        mock_response = MagicMock()
        mock_response.status_code = 429
        mock_response.headers = {"Retry-After": "60"}
        mock_response.text = '{"error": {"message": "Resource exhausted"}}'
        mock_post.return_value = mock_response

        provider = GeminiEmbeddingProvider(api_key="key")
        with self.assertRaises(EmbeddingQuotaExhaustedError):
            provider.embed_texts(["Doc"])
