"""
Comprehensive Offline Test Suite for Pwanimate Phase 5 AI Gateway.

Tests data contracts, Mock provider, Gemini provider, Groq provider,
OpenRouter provider, LLMRouter, AIGateway facade, and ContextPackage integration.
All external HTTP calls are strictly mocked with zero network dependencies.
"""

from unittest.mock import MagicMock, patch
import requests
from django.test import SimpleTestCase

from pwanimate.ai.exceptions import (
    AIGatewayError,
    AIProviderAPIError,
    AIProviderAuthenticationError,
    AIProviderConfigurationError,
    AIProviderRateLimitError,
    AIProviderTimeoutError,
)
from pwanimate.ai.gateway import (
    AIGateway,
    ChatMessage,
    LLMRequest,
    LLMResponse,
    LLMRouter,
    get_quota_tracker,
)
from pwanimate.ai.providers import (
    GeminiLLMProvider,
    GroqLLMProvider,
    MockLLMProvider,
    OpenRouterLLMProvider,
)
from pwanimate.context.types import ContextItem, ContextPackage


class ContractsTestCase(SimpleTestCase):
    """Test ChatMessage, LLMRequest, and LLMResponse data contracts."""

    def test_chat_message_valid(self):
        msg = ChatMessage(role="user", content="Hello world")
        self.assertEqual(msg.role, "user")
        self.assertEqual(msg.content, "Hello world")
        self.assertEqual(msg.to_dict(), {"role": "user", "content": "Hello world"})

    def test_chat_message_invalid_role(self):
        with self.assertRaises(ValueError):
            ChatMessage(role="invalid_role", content="Hello")

    def test_chat_message_invalid_content_type(self):
        with self.assertRaises(TypeError):
            ChatMessage(role="user", content=123)  # type: ignore

    def test_llm_request_valid_defaults(self):
        req = LLMRequest(
            messages=[ChatMessage(role="user", content="Test query")],
        )
        self.assertEqual(req.task, "general")
        self.assertEqual(req.temperature, 0.2)
        self.assertEqual(req.max_tokens, 1024)
        self.assertIsNone(req.context)
        self.assertIsNone(req.system_instruction)

    def test_llm_request_rejects_non_text_system_instruction(self):
        for value in (("first", "second"), ["instruction"], {"text": "instruction"}, 1):
            with self.subTest(value=value), self.assertRaisesRegex(TypeError, "system_instruction"):
                LLMRequest(system_instruction=value)

    def test_planner_instruction_serializes_as_text_for_both_providers(self):
        # Evaluate the real prompt assignment without running tools, ORM, or an LLM.
        import ast
        from pathlib import Path
        planner_path = Path(__file__).resolve().parents[1] / "orchestrator" / "agent.py"
        tree = ast.parse(planner_path.read_text())
        agent = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "StudyAgent")
        run = next(node for node in agent.body if isinstance(node, ast.FunctionDef) and node.name == "run")
        assignment = next(node for node in run.body if isinstance(node, ast.Assign)
                          and any(isinstance(target, ast.Name) and target.id == "prompt" for target in node.targets))
        import json
        prompt = eval(compile(ast.Expression(assignment.value), str(planner_path), "eval"),
                      {"json": json, "schemas": []})
        self.assertIsInstance(prompt, str)
        self.assertIn('"calls"', prompt)
        request = LLMRequest(task="tool", system_instruction=prompt,
                             messages=[ChatMessage(role="user", content="Find my study materials")])
        gemini = GeminiLLMProvider(api_key="test", model_name="test")
        groq = GroqLLMProvider(api_key="test", model_name="test")
        self.assertEqual(gemini._build_payload(request)["systemInstruction"]["parts"][0]["text"], prompt)
        self.assertEqual(groq._build_payload(request, "test")["messages"][0]["content"], prompt)

    def test_llm_request_invalid_temperature(self):
        with self.assertRaises(ValueError):
            LLMRequest(temperature=2.5)

        with self.assertRaises(ValueError):
            LLMRequest(temperature=-0.1)

    def test_llm_request_invalid_max_tokens(self):
        with self.assertRaises(ValueError):
            LLMRequest(max_tokens=0)

    def test_llm_response_to_dict(self):
        resp = LLMResponse(
            content="Generated answer.",
            provider="test_provider",
            model="test-model",
            finish_reason="stop",
            prompt_tokens=15,
            completion_tokens=25,
            total_tokens=40,
            citations=["[Doc1, p. 1]"],
            metadata={"latency_ms": 12.5},
        )
        data = resp.to_dict()
        self.assertEqual(data["content"], "Generated answer.")
        self.assertEqual(data["provider"], "test_provider")
        self.assertEqual(data["model"], "test-model")
        self.assertEqual(data["finish_reason"], "stop")
        self.assertEqual(data["prompt_tokens"], 15)
        self.assertEqual(data["completion_tokens"], 25)
        self.assertEqual(data["total_tokens"], 40)
        self.assertEqual(data["citations"], ["[Doc1, p. 1]"])
        self.assertEqual(data["metadata"]["latency_ms"], 12.5)


class MockLLMProviderTestCase(SimpleTestCase):
    """Test deterministic MockLLMProvider behavior."""

    def test_mock_provider_generation(self):
        provider = MockLLMProvider()
        self.assertTrue(provider.is_available())

        req = LLMRequest(
            task="qa",
            messages=[ChatMessage(role="user", content="Explain packet routing")],
        )
        resp = provider.generate(req)

        self.assertEqual(resp.provider, "mock")
        self.assertEqual(resp.model, "mock-llm-v1")
        self.assertEqual(resp.finish_reason, "stop")
        self.assertIn("Explain packet routing", resp.content)
        self.assertGreater(resp.total_tokens, 0)
        self.assertEqual(provider.call_count, 1)

    def test_mock_provider_with_context_citations(self):
        provider = MockLLMProvider()
        pkg = ContextPackage(
            query="Networking",
            citations=["[Computer Networks, v1: p. 42]"],
            total_items=1,
            total_characters=50,
        )
        req = LLMRequest(
            task="rag",
            messages=[ChatMessage(role="user", content="What is routing?")],
            context=pkg,
        )
        resp = provider.generate(req)

        self.assertEqual(resp.citations, ["[Computer Networks, v1: p. 42]"])
        self.assertIn("1 context items", resp.content)

    def test_mock_provider_simulated_error(self):
        provider = MockLLMProvider(simulated_error=AIProviderTimeoutError("Simulated timeout", provider="mock"))
        req = LLMRequest(messages=[ChatMessage(role="user", content="Hi")])
        with self.assertRaises(AIProviderTimeoutError):
            provider.generate(req)


class LLMRouterTestCase(SimpleTestCase):
    """Test static LLMRouter resolution and no-fallback policy."""

    def test_explicit_provider_selection(self):
        mock_provider = MockLLMProvider()
        router = LLMRouter(providers={"mock": mock_provider}, default_provider="gemini")
        req = LLMRequest(provider="mock", messages=[ChatMessage(role="user", content="Hi")])
        resolved = router.resolve_provider(req)
        self.assertIs(resolved, mock_provider)

    def test_default_provider_selection(self):
        mock_provider = MockLLMProvider()
        router = LLMRouter(providers={"mock": mock_provider}, default_provider="mock")
        req = LLMRequest(messages=[ChatMessage(role="user", content="Hi")])
        resolved = router.resolve_provider(req)
        self.assertIs(resolved, mock_provider)

    def test_unknown_provider_raises_error(self):
        router = LLMRouter()
        req = LLMRequest(provider="nonexistent_provider", messages=[ChatMessage(role="user", content="Hi")])
        with self.assertRaises(AIProviderConfigurationError):
            router.resolve_provider(req)

    def test_unavailable_provider_raises_error_without_fallback(self):
        """Verify that an unavailable provider does not automatically fallback."""
        groq_unconfigured = GroqLLMProvider(api_key="")
        mock_provider = MockLLMProvider()
        router = LLMRouter(
            providers={"groq": groq_unconfigured, "mock": mock_provider},
            default_provider="groq",
        )
        req = LLMRequest(messages=[ChatMessage(role="user", content="Hi")])
        with self.assertRaises(AIProviderConfigurationError) as cm:
            router.resolve_provider(req)
        self.assertIn("Automatic cross-provider fallback is prohibited", str(cm.exception))


class AIGatewayTestCase(SimpleTestCase):
    """Test AIGateway facade, validation, and error propagation."""

    def setUp(self):
        super().setUp()
        get_quota_tracker().clear()

    def tearDown(self):
        get_quota_tracker().clear()
        super().tearDown()

    def test_gateway_empty_request_raises_value_error(self):
        gateway = AIGateway()
        req = LLMRequest(messages=[])
        with self.assertRaises(ValueError):
            gateway.generate(req)

    def test_gateway_invalid_type_raises_type_error(self):
        gateway = AIGateway()
        with self.assertRaises(TypeError):
            gateway.generate("invalid")  # type: ignore

    def test_gateway_successful_generation(self):
        mock_provider = MockLLMProvider()
        router = LLMRouter(providers={"mock": mock_provider}, default_provider="mock")
        gateway = AIGateway(router=router)

        req = LLMRequest(
            task="summary",
            messages=[ChatMessage(role="user", content="Summarize chapter 1")],
        )
        resp = gateway.generate(req)

        self.assertEqual(resp.provider, "mock")
        self.assertIn("Summarize chapter 1", resp.content)
        self.assertIn("latency_ms", resp.metadata)
        self.assertGreaterEqual(resp.metadata["latency_ms"], 0.0)

    def test_gateway_propagates_provider_exceptions(self):
        mock_provider = MockLLMProvider(simulated_error=AIProviderRateLimitError("Rate limit hit", provider="mock"))
        router = LLMRouter(providers={"mock": mock_provider}, default_provider="mock")
        gateway = AIGateway(router=router)

        req = LLMRequest(messages=[ChatMessage(role="user", content="Hi")])
        with self.assertRaises(AIProviderRateLimitError):
            gateway.generate(req)


class GeminiLLMProviderTestCase(SimpleTestCase):
    """Test GeminiLLMProvider with mocked HTTP responses."""

    def test_gemini_unconfigured_availability(self):
        provider = GeminiLLMProvider(api_key="")
        self.assertFalse(provider.is_available())
        req = LLMRequest(messages=[ChatMessage(role="user", content="Hi")])
        with self.assertRaises(AIProviderConfigurationError):
            provider.generate(req)

    @patch("requests.Session.post")
    def test_gemini_successful_generation(self, mock_post):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "candidates": [
                {
                    "content": {
                        "parts": [{"text": "Gemini generated response."}],
                        "role": "model",
                    },
                    "finishReason": "STOP",
                }
            ],
            "usageMetadata": {
                "promptTokenCount": 20,
                "candidatesTokenCount": 10,
                "totalTokenCount": 30,
            },
        }
        mock_post.return_value = mock_resp

        provider = GeminiLLMProvider(api_key="test-gemini-key", model_name="gemini-2.5-flash")
        self.assertTrue(provider.is_available())

        req = LLMRequest(
            task="rag",
            system_instruction="You are an academic tutor.",
            messages=[
                ChatMessage(role="user", content="What is DNS?"),
                ChatMessage(role="assistant", content="DNS resolves domain names."),
                ChatMessage(role="user", content="How does caching work?"),
            ],
            temperature=0.4,
            max_tokens=512,
        )
        resp = provider.generate(req)

        self.assertEqual(resp.content, "Gemini generated response.")
        self.assertEqual(resp.provider, "gemini")
        self.assertEqual(resp.model, "gemini-2.5-flash")
        self.assertEqual(resp.finish_reason, "stop")
        self.assertEqual(resp.prompt_tokens, 20)
        self.assertEqual(resp.completion_tokens, 10)
        self.assertEqual(resp.total_tokens, 30)

        # Verify payload structure sent to Gemini
        call_args = mock_post.call_args
        self.assertIn("https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent", call_args[0][0])
        self.assertEqual(call_args[1]["headers"]["x-goog-api-key"], "test-gemini-key")
        payload = call_args[1]["json"]
        self.assertEqual(payload["systemInstruction"]["parts"][0]["text"], "You are an academic tutor.")
        self.assertEqual(len(payload["contents"]), 3)
        self.assertEqual(payload["contents"][0]["role"], "user")
        self.assertEqual(payload["contents"][1]["role"], "model")
        self.assertEqual(payload["contents"][2]["role"], "user")
        self.assertEqual(payload["generationConfig"]["temperature"], 0.4)
        self.assertEqual(payload["generationConfig"]["maxOutputTokens"], 512)

    @patch("requests.Session.post")
    def test_gemini_authentication_error(self, mock_post):
        mock_resp = MagicMock()
        mock_resp.status_code = 403
        mock_resp.json.return_value = {"error": {"message": "API key not valid"}}
        mock_post.return_value = mock_resp

        provider = GeminiLLMProvider(api_key="bad-key")
        req = LLMRequest(messages=[ChatMessage(role="user", content="Hi")])
        with self.assertRaises(AIProviderAuthenticationError):
            provider.generate(req)

    @patch("requests.Session.post")
    def test_gemini_rate_limit_error(self, mock_post):
        mock_resp = MagicMock()
        mock_resp.status_code = 429
        mock_resp.json.return_value = {"error": {"message": "Quota exceeded"}}
        mock_post.return_value = mock_resp

        provider = GeminiLLMProvider(api_key="valid-key")
        req = LLMRequest(messages=[ChatMessage(role="user", content="Hi")])
        with self.assertRaises(AIProviderRateLimitError):
            provider.generate(req)

    @patch("requests.Session.post")
    def test_gemini_timeout_error(self, mock_post):
        mock_post.side_effect = requests.exceptions.Timeout("Connection timed out")

        provider = GeminiLLMProvider(api_key="valid-key")
        req = LLMRequest(messages=[ChatMessage(role="user", content="Hi")])
        with self.assertRaises(AIProviderTimeoutError):
            provider.generate(req)

    @patch("requests.Session.post")
    def test_gemini_server_error(self, mock_post):
        mock_resp = MagicMock()
        mock_resp.status_code = 503
        mock_resp.json.return_value = {"error": {"message": "Service unavailable"}}
        mock_post.return_value = mock_resp

        provider = GeminiLLMProvider(api_key="valid-key")
        req = LLMRequest(messages=[ChatMessage(role="user", content="Hi")])
        with self.assertRaises(AIProviderAPIError):
            provider.generate(req)

    @patch("requests.Session.post")
    def test_gemini_thinking_token_accounting(self, mock_post):
        """Test extraction of thoughtsTokenCount, total_output_tokens, and max_output_tokens."""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "candidates": [
                {
                    "content": {
                        "parts": [{"text": "Visible answer text truncated by max_tokens limit"}],
                        "role": "model",
                    },
                    "finishReason": "MAX_TOKENS",
                }
            ],
            "usageMetadata": {
                "promptTokenCount": 1878,
                "candidatesTokenCount": 131,
                "thoughtsTokenCount": 889,
                "totalTokenCount": 2898,
            },
        }
        mock_post.return_value = mock_resp

        provider = GeminiLLMProvider(api_key="test-gemini-key", model_name="gemini-3.6-flash")
        req = LLMRequest(
            task="rag",
            messages=[ChatMessage(role="user", content="Explain subnetting")],
            temperature=0.2,
            max_tokens=1024,
        )
        resp = provider.generate(req)

        # Provider-neutral contract checks
        self.assertEqual(resp.provider, "gemini")
        self.assertEqual(resp.model, "gemini-3.6-flash")
        self.assertEqual(resp.finish_reason, "max_tokens")
        self.assertEqual(resp.prompt_tokens, 1878)
        self.assertEqual(resp.completion_tokens, 131)
        self.assertEqual(resp.total_tokens, 2898)

        # Diagnostic metadata checks
        self.assertEqual(resp.metadata.get("thoughts_tokens"), 889)
        self.assertEqual(resp.metadata.get("total_output_tokens"), 1020)
        self.assertEqual(resp.metadata.get("max_output_tokens"), 1024)
        self.assertEqual(resp.metadata.get("gemini_finish_reason"), "MAX_TOKENS")

        # Arithmetic verification: prompt + thoughts + completion == total
        self.assertEqual(
            resp.prompt_tokens + resp.metadata["thoughts_tokens"] + resp.completion_tokens,
            resp.total_tokens,
        )
        # Arithmetic verification: thoughts + completion == total_output
        self.assertEqual(
            resp.metadata["thoughts_tokens"] + resp.completion_tokens,
            resp.metadata["total_output_tokens"],
        )
        # Ceiling verification: total_output <= max_output_tokens
        self.assertLessEqual(
            resp.metadata["total_output_tokens"],
            resp.metadata["max_output_tokens"],
        )

    @patch("requests.Session.post")
    def test_gemini_non_thinking_response(self, mock_post):
        """Test response handling when thoughtsTokenCount is absent in usageMetadata."""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "candidates": [
                {
                    "content": {
                        "parts": [{"text": "Simple answer"}],
                        "role": "model",
                    },
                    "finishReason": "STOP",
                }
            ],
            "usageMetadata": {
                "promptTokenCount": 20,
                "candidatesTokenCount": 10,
                "totalTokenCount": 30,
            },
        }
        mock_post.return_value = mock_resp

        provider = GeminiLLMProvider(api_key="test-gemini-key", model_name="gemini-2.5-flash")
        req = LLMRequest(
            messages=[ChatMessage(role="user", content="Hello")],
            max_tokens=512,
        )
        resp = provider.generate(req)

        self.assertIsNone(resp.metadata.get("thoughts_tokens"))
        self.assertEqual(resp.metadata.get("total_output_tokens"), 10)
        self.assertEqual(resp.metadata.get("max_output_tokens"), 512)
        self.assertEqual(resp.finish_reason, "stop")


class GroqLLMProviderTestCase(SimpleTestCase):
    """Test GroqLLMProvider with mocked HTTP responses."""

    def test_groq_unconfigured_availability(self):
        provider = GroqLLMProvider(api_key="")
        self.assertFalse(provider.is_available())
        req = LLMRequest(messages=[ChatMessage(role="user", content="Hi")])
        with self.assertRaises(AIProviderConfigurationError):
            provider.generate(req)

    @patch("requests.Session.post")
    def test_groq_successful_generation(self, mock_post):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "id": "chatcmpl-groq-123",
            "choices": [
                {
                    "message": {"role": "assistant", "content": "Groq generated response."},
                    "finish_reason": "stop",
                }
            ],
            "usage": {
                "prompt_tokens": 12,
                "completion_tokens": 8,
                "total_tokens": 20,
            },
        }
        mock_post.return_value = mock_resp

        provider = GroqLLMProvider(api_key="gsk_test_key", model_name="llama-3.3-70b-versatile")
        self.assertTrue(provider.is_available())

        req = LLMRequest(
            task="general",
            system_instruction="Be concise.",
            messages=[ChatMessage(role="user", content="Define subnetting.")],
        )
        resp = provider.generate(req)

        self.assertEqual(resp.content, "Groq generated response.")
        self.assertEqual(resp.provider, "groq")
        self.assertEqual(resp.model, "llama-3.3-70b-versatile")
        self.assertEqual(resp.finish_reason, "stop")
        self.assertEqual(resp.prompt_tokens, 12)
        self.assertEqual(resp.completion_tokens, 8)
        self.assertEqual(resp.total_tokens, 20)

        # Check authorization header and payload
        call_args = mock_post.call_args
        self.assertEqual(call_args[1]["headers"]["Authorization"], "Bearer gsk_test_key")
        payload = call_args[1]["json"]
        self.assertEqual(payload["messages"][0], {"role": "system", "content": "Be concise."})
        self.assertEqual(payload["messages"][1], {"role": "user", "content": "Define subnetting."})

    @patch("requests.Session.post")
    def test_groq_authentication_error(self, mock_post):
        mock_resp = MagicMock()
        mock_resp.status_code = 401
        mock_resp.json.return_value = {"error": {"message": "Invalid API Key"}}
        mock_post.return_value = mock_resp

        provider = GroqLLMProvider(api_key="bad-key")
        req = LLMRequest(messages=[ChatMessage(role="user", content="Hi")])
        with self.assertRaises(AIProviderAuthenticationError):
            provider.generate(req)

    @patch("requests.Session.post")
    def test_groq_rate_limit_error(self, mock_post):
        mock_resp = MagicMock()
        mock_resp.status_code = 429
        mock_resp.json.return_value = {"error": {"message": "Rate limit reached"}}
        mock_post.return_value = mock_resp

        provider = GroqLLMProvider(api_key="valid-key")
        req = LLMRequest(messages=[ChatMessage(role="user", content="Hi")])
        with self.assertRaises(AIProviderRateLimitError):
            provider.generate(req)


class OpenRouterLLMProviderTestCase(SimpleTestCase):
    """Test OpenRouterLLMProvider with mocked HTTP responses."""

    def test_openrouter_unconfigured_availability(self):
        provider = OpenRouterLLMProvider(api_key="")
        self.assertFalse(provider.is_available())
        req = LLMRequest(messages=[ChatMessage(role="user", content="Hi")])
        with self.assertRaises(AIProviderConfigurationError):
            provider.generate(req)

    @patch("requests.Session.post")
    def test_openrouter_successful_generation(self, mock_post):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "id": "gen-openrouter-456",
            "choices": [
                {
                    "message": {"role": "assistant", "content": "OpenRouter response."},
                    "finish_reason": "stop",
                }
            ],
            "usage": {
                "prompt_tokens": 15,
                "completion_tokens": 10,
                "total_tokens": 25,
            },
        }
        mock_post.return_value = mock_resp

        provider = OpenRouterLLMProvider(api_key="sk-or-test-key")
        self.assertTrue(provider.is_available())

        req = LLMRequest(
            task="chat",
            messages=[ChatMessage(role="user", content="Hello")],
        )
        resp = provider.generate(req)

        self.assertEqual(resp.content, "OpenRouter response.")
        self.assertEqual(resp.provider, "openrouter")
        self.assertEqual(resp.total_tokens, 25)

        # Check attribution headers
        call_args = mock_post.call_args
        self.assertEqual(call_args[1]["headers"]["Authorization"], "Bearer sk-or-test-key")
        self.assertEqual(call_args[1]["headers"]["HTTP-Referer"], "https://vasto.local")
        self.assertEqual(call_args[1]["headers"]["X-Title"], "Pwanimate")

    @patch("requests.Session.post")
    def test_openrouter_reasoning_model_budget_and_details(self, mock_post):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "id": "gen-openrouter-nex",
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": "Here is the verified answer.",
                        "reasoning": "Step 1: Analyzed query. Step 2: Formulated answer.",
                    },
                    "finish_reason": "stop",
                }
            ],
            "usage": {
                "prompt_tokens": 2330,
                "completion_tokens": 1200,
                "total_tokens": 3530,
                "completion_tokens_details": {
                    "reasoning_tokens": 900,
                },
            },
        }
        mock_post.return_value = mock_resp

        provider = OpenRouterLLMProvider(api_key="sk-or-test-key")
        req = LLMRequest(
            task="rag",
            messages=[ChatMessage(role="user", content="Explain subnetting")],
            model="nex-agi/nex-n2.5-pro:free",
            max_tokens=1024,
        )
        resp = provider.generate(req)

        # Verify payload increased token budget and included reasoning effort
        call_args = mock_post.call_args
        sent_payload = call_args[1]["json"]
        self.assertEqual(sent_payload["max_tokens"], 2048)
        self.assertEqual(sent_payload["reasoning"], {"effort": "low"})

        # Verify response parsing
        self.assertEqual(resp.content, "Here is the verified answer.")
        self.assertEqual(resp.finish_reason, "stop")
        self.assertEqual(resp.metadata["thoughts_tokens"], 900)
        self.assertEqual(resp.metadata["total_output_tokens"], 1200)
        self.assertNotIn("reasoning", resp.metadata)

    @patch("requests.Session.post")
    def test_openrouter_empty_content_length_raises_api_error(self, mock_post):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "id": "gen-openrouter-trunc",
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": "",
                        "reasoning": "Thinking process interrupted by length limit...",
                    },
                    "finish_reason": "length",
                }
            ],
            "usage": {
                "prompt_tokens": 2330,
                "completion_tokens": 1024,
                "total_tokens": 3354,
                "completion_tokens_details": {
                    "reasoning_tokens": 1024,
                },
            },
        }
        mock_post.return_value = mock_resp

        provider = OpenRouterLLMProvider(api_key="sk-or-test-key")
        req = LLMRequest(
            task="rag",
            messages=[ChatMessage(role="user", content="Complex prompt")],
            model="nex-agi/nex-n2.5-pro:free",
            max_tokens=1024,
        )
        with self.assertRaises(AIProviderAPIError) as cm:
            provider.generate(req)
        self.assertIn("reached maximum token limit", str(cm.exception))

    @patch("requests.Session.post")
    def test_openrouter_reasoning_only_stop_fails_closed(self, mock_post):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "id": "gen-openrouter-reasoning-only",
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": "",
                        "reasoning": "This model put its final answer inside the reasoning field.",
                    },
                    "finish_reason": "stop",
                }
            ],
            "usage": {
                "prompt_tokens": 100,
                "completion_tokens": 50,
                "total_tokens": 150,
            },
        }
        mock_post.return_value = mock_resp

        provider = OpenRouterLLMProvider(api_key="sk-or-test-key")
        req = LLMRequest(
            task="chat",
            messages=[ChatMessage(role="user", content="Hello")],
        )
        with self.assertRaises(AIProviderAPIError):
            provider.generate(req)

    @patch("requests.Session.post")
    def test_openrouter_empty_content_raises_api_error(self, mock_post):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "id": "gen-openrouter-blank",
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": "   ",
                    },
                    "finish_reason": "stop",
                }
            ],
            "usage": {
                "prompt_tokens": 50,
                "completion_tokens": 5,
                "total_tokens": 55,
            },
        }
        mock_post.return_value = mock_resp

        provider = OpenRouterLLMProvider(api_key="sk-or-test-key")
        req = LLMRequest(
            task="chat",
            messages=[ChatMessage(role="user", content="Hello")],
        )
        with self.assertRaises(AIProviderAPIError) as cm:
            provider.generate(req)
        self.assertIn("generated empty content", str(cm.exception))

    @patch("requests.Session.post")
    def test_groq_reasoning_tokens_and_empty_check(self, mock_post):
        # 1. Success with reasoning tokens
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "id": "gen-groq-r1",
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": "Groq answer.",
                        "reasoning": "Groq thinking.",
                    },
                    "finish_reason": "stop",
                }
            ],
            "usage": {
                "prompt_tokens": 500,
                "completion_tokens": 200,
                "total_tokens": 700,
                "completion_tokens_details": {
                    "reasoning_tokens": 150,
                },
            },
        }
        mock_post.return_value = mock_resp

        from pwanimate.ai.providers.groq import GroqLLMProvider
        provider = GroqLLMProvider(api_key="sk-groq-key")
        req = LLMRequest(messages=[ChatMessage(role="user", content="Hi")])
        resp = provider.generate(req)

        self.assertEqual(resp.content, "Groq answer.")
        self.assertEqual(resp.metadata["thoughts_tokens"], 150)
        self.assertEqual(resp.metadata["total_output_tokens"], 200)

        # 2. Empty content raises error
        mock_resp.json.return_value = {
            "id": "gen-groq-empty",
            "choices": [
                {
                    "message": {"role": "assistant", "content": ""},
                    "finish_reason": "stop",
                }
            ],
            "usage": {"prompt_tokens": 50, "completion_tokens": 10, "total_tokens": 60},
        }
        with self.assertRaises(AIProviderAPIError):
            provider.generate(req)


class ContextIntegrationTestCase(SimpleTestCase):
    """Test end-to-end integration with Phase 4 ContextPackage."""

    def setUp(self):
        super().setUp()
        get_quota_tracker().clear()

    def tearDown(self):
        get_quota_tracker().clear()
        super().tearDown()

    def test_context_package_travels_through_gateway_to_provider(self):
        # Create a realistic Phase 4 ContextPackage
        item1 = ContextItem(
            source="document",
            object_id=101,
            title="Operating Systems Notes",
            content="Page 12: Virtual memory and page replacement algorithms like LRU.",
            citation="[OS Notes, v1: p. 12]",
            url="/documents/document/os-notes/",
            relevance_score=0.92,
        )
        item2 = ContextItem(
            source="post",
            object_id=202,
            title="Discussion on Page Faults",
            content="Thread discussing FIFO vs LRU anomalies in exam questions.",
            citation="Post by @student_alice",
            url="/post/share-post-202/",
            relevance_score=0.85,
        )
        pkg = ContextPackage(
            query="Explain virtual memory and LRU",
            items=[item1, item2],
            citations=["[OS Notes, v1: p. 12]", "Post by @student_alice"],
            total_items=2,
            estimated_tokens=50,
            total_characters=200,
        )

        mock_provider = MockLLMProvider()
        router = LLMRouter(providers={"mock": mock_provider}, default_provider="mock")
        gateway = AIGateway(router=router)

        req = LLMRequest(
            task="rag",
            messages=[ChatMessage(role="user", content="What is LRU page replacement?")],
            context=pkg,
        )

        resp = gateway.generate(req)

        # Citations must be preserved
        self.assertEqual(len(resp.citations), 2)
        self.assertIn("[OS Notes, v1: p. 12]", resp.citations)
        self.assertIn("Post by @student_alice", resp.citations)
        self.assertEqual(mock_provider.last_request.context, pkg)

    @patch("requests.Session.post")
    def test_gemini_and_groq_receive_formatted_context_xml(self, mock_post):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "candidates": [{"content": {"parts": [{"text": "Answer"}]}, "finishReason": "STOP"}],
            "usageMetadata": {},
        }
        mock_post.return_value = mock_resp

        item = ContextItem(
            source="document",
            object_id=5,
            title="Data Structures",
            content="Hash table collision resolution via chaining.",
            citation="[DS, v1: p. 5]",
        )
        pkg = ContextPackage(
            query="Hash collisions",
            items=[item],
            citations=["[DS, v1: p. 5]"],
            total_items=1,
            estimated_tokens=25,
            total_characters=100,
        )

        provider = GeminiLLMProvider(api_key="key")
        req = LLMRequest(
            messages=[ChatMessage(role="user", content="How does chaining work?")],
            context=pkg,
        )
        provider.generate(req)

        call_args = mock_post.call_args
        payload = call_args[1]["json"]
        user_parts = payload["contents"][0]["parts"]
        # Grounding XML must be enclosed and present
        self.assertIn("<retrieved_context", user_parts[0]["text"])
        self.assertIn("<grounding_data source=\"document\"", user_parts[0]["text"])
        self.assertIn("Hash table collision resolution", user_parts[0]["text"])
        self.assertEqual(user_parts[1]["text"], "How does chaining work?")


class AutoModelSwitchingTestCase(SimpleTestCase):
    """Test auto model switching on rate limits and quota tracking."""

    def setUp(self):
        super().setUp()
        get_quota_tracker().clear()

    def tearDown(self):
        get_quota_tracker().clear()
        super().tearDown()

    def test_auto_model_switching_within_same_provider(self):
        class SwitchingMockProvider(MockLLMProvider):
            def generate(self, request: LLMRequest) -> LLMResponse:
                if request.model == "mock-tier-1":
                    raise AIProviderRateLimitError("Free tier quota exhausted for mock-tier-1", provider="mock")
                return super().generate(request)

        mock_prov = SwitchingMockProvider(model_name="mock-tier-1")
        router = LLMRouter(providers={"mock": mock_prov}, default_provider="mock")
        gateway = AIGateway(router=router)

        with self.settings(PWANIMATE_LLM_FALLBACK_CHAIN=[
            {"provider": "mock", "model": "mock-tier-1"},
            {"provider": "mock", "model": "mock-tier-2"},
        ]):
            req = LLMRequest(
                messages=[ChatMessage(role="user", content="Hello")],
                provider="mock",
                model="mock-tier-1",
            )
            resp = gateway.generate(req)

            self.assertEqual(resp.model, "mock-tier-2")
            quota_info = resp.metadata.get("quota_info")
            self.assertIsNotNone(quota_info)
            self.assertTrue(quota_info["switched"])
            self.assertEqual(quota_info["active_model"], "mock-tier-2")
            self.assertEqual(quota_info["primary_model"], "mock-tier-1")
            self.assertEqual(len(quota_info["skipped"]), 1)
            self.assertEqual(quota_info["skipped"][0]["model"], "mock-tier-1")
            self.assertEqual(quota_info["skipped"][0]["reason"], "rate_limit")

    def test_cooldown_skips_depleted_model_automatically(self):
        call_models = []

        class TrackingMockProvider(MockLLMProvider):
            def generate(self, request: LLMRequest) -> LLMResponse:
                call_models.append(request.model)
                if request.model == "model-a":
                    raise AIProviderRateLimitError("Quota reached for model-a", provider="mock")
                return super().generate(request)

        mock_prov = TrackingMockProvider(model_name="model-a")
        router = LLMRouter(providers={"mock": mock_prov}, default_provider="mock")
        gateway = AIGateway(router=router)

        with self.settings(
            PWANIMATE_LLM_FALLBACK_CHAIN=[
                {"provider": "mock", "model": "model-a"},
                {"provider": "mock", "model": "model-b"},
            ],
            PWANIMATE_RATE_LIMIT_COOLDOWN=60.0,
        ):
            # First turn: model-a is attempted, fails with 429, switches to model-b
            req1 = LLMRequest(messages=[ChatMessage(role="user", content="Turn 1")], provider="mock", model="model-a")
            resp1 = gateway.generate(req1)
            self.assertEqual(resp1.model, "model-b")
            self.assertEqual(call_models, ["model-a", "model-b"])

            # Second turn: model-a is in cooldown, should be skipped directly without calling it
            call_models.clear()
            req2 = LLMRequest(messages=[ChatMessage(role="user", content="Turn 2")], provider="mock", model="model-a")
            resp2 = gateway.generate(req2)
            self.assertEqual(resp2.model, "model-b")
            # Only model-b was called! model-a was skipped due to cooldown
            self.assertEqual(call_models, ["model-b"])
            self.assertEqual(resp2.metadata["quota_info"]["skipped"][0]["reason"], "cooldown")

    def test_candidate_api_error_falls_back_to_next_candidate(self):
        """When a candidate in the fallback chain fails with 404/API error, fallback continues."""
        class ErrorFallbackProvider(MockLLMProvider):
            def generate(self, request: LLMRequest) -> LLMResponse:
                if request.model == "model-429":
                    raise AIProviderRateLimitError("Rate limit hit", provider="mock")
                elif request.model == "model-404":
                    raise AIProviderAPIError("Model is no longer available (404)", provider="mock", status_code=404)
                elif request.model == "model-valid":
                    return super().generate(request)
                raise ValueError("Unexpected model")

        mock_prov = ErrorFallbackProvider(model_name="model-429")
        router = LLMRouter(providers={"mock": mock_prov}, default_provider="mock")
        gateway = AIGateway(router=router)

        with self.settings(
            PWANIMATE_LLM_FALLBACK_CHAIN=[
                {"provider": "mock", "model": "model-429"},
                {"provider": "mock", "model": "model-404"},
                {"provider": "mock", "model": "model-valid"},
            ],
        ):
            req = LLMRequest(messages=[ChatMessage(role="user", content="Test fallback")], provider="mock", model="model-429")
            resp = gateway.generate(req)
            self.assertEqual(resp.model, "model-valid")
            skipped = resp.metadata["quota_info"]["skipped"]
            self.assertEqual(len(skipped), 2)
            self.assertEqual(skipped[0]["model"], "model-429")
            self.assertEqual(skipped[0]["reason"], "rate_limit")
            self.assertEqual(skipped[1]["model"], "model-404")
            self.assertEqual(skipped[1]["reason"], "error: AIProviderAPIError")


class TelemetryAndFinishReasonTestCase(SimpleTestCase):
    """Tests for finish_reason propagation and boundary telemetry contracts."""

    def test_orchestration_response_finish_reason_in_to_dict(self):
        from pwanimate.orchestrator.types import OrchestrationResponse
        resp = OrchestrationResponse(
            answer="Partial answer...",
            finish_reason="max_tokens",
            prompt_tokens=50,
            completion_tokens=1024,
            total_tokens=1074,
        )
        self.assertEqual(resp.finish_reason, "max_tokens")
        data = resp.to_dict()
        self.assertIn("finish_reason", data)
        self.assertEqual(data["finish_reason"], "max_tokens")
        self.assertEqual(data["completion_tokens"], 1024)

    def test_orchestrator_propagates_finish_reason(self):
        from pwanimate.orchestrator.service import PwanimateOrchestrator
        from pwanimate.orchestrator.types import OrchestrationRequest
        from pwanimate.retrieval.services.unified_retrieval import UnifiedRetrievalService

        class TruncatedMockProvider(MockLLMProvider):
            def generate(self, request: LLMRequest) -> LLMResponse:
                return LLMResponse(
                    content="This explanation was cut off at the limit...",
                    provider=self.provider_name,
                    model=self.model_name,
                    finish_reason="max_tokens",
                    prompt_tokens=120,
                    completion_tokens=1024,
                    total_tokens=1144,
                )

        mock_prov = TruncatedMockProvider(model_name="mock-truncated")
        router = LLMRouter(providers={"mock": mock_prov}, default_provider="mock")
        gateway = AIGateway(router=router)

        mock_retrieval = MagicMock(spec=UnifiedRetrievalService)
        mock_retrieval.retrieve.return_value = MagicMock(results=[])

        orchestrator = PwanimateOrchestrator(
            retrieval_service=mock_retrieval,
            gateway=gateway,
        )

        req = OrchestrationRequest(query="Explain normalization in detail", task="general", provider="mock")
        resp = orchestrator.run(req)

        self.assertEqual(resp.finish_reason, "max_tokens")
        self.assertEqual(resp.completion_tokens, 1024)
        data = resp.to_dict()
        self.assertEqual(data["finish_reason"], "max_tokens")
        self.assertEqual(data["completion_tokens"], 1024)

    def test_orchestrator_propagates_thinking_metadata(self):
        from pwanimate.orchestrator.service import PwanimateOrchestrator
        from pwanimate.orchestrator.types import OrchestrationRequest
        from pwanimate.retrieval.services.unified_retrieval import UnifiedRetrievalService

        class ThinkingMockProvider(MockLLMProvider):
            def generate(self, request: LLMRequest) -> LLMResponse:
                return LLMResponse(
                    content="Thinking response answer",
                    provider="mock",
                    model="mock-thinking",
                    finish_reason="max_tokens",
                    prompt_tokens=1878,
                    completion_tokens=131,
                    total_tokens=2898,
                    metadata={
                        "thoughts_tokens": 889,
                        "total_output_tokens": 1020,
                        "max_output_tokens": request.max_tokens,
                    },
                )

        mock_prov = ThinkingMockProvider()
        router = LLMRouter(providers={"mock": mock_prov}, default_provider="mock")
        gateway = AIGateway(router=router)

        mock_retrieval = MagicMock(spec=UnifiedRetrievalService)
        mock_retrieval.retrieve.return_value = MagicMock(results=[])

        orchestrator = PwanimateOrchestrator(
            retrieval_service=mock_retrieval,
            gateway=gateway,
        )

        req = OrchestrationRequest(
            query="Explain subnetting",
            task="general",
            provider="mock",
            max_tokens=1024,
        )
        resp = orchestrator.run(req)

        self.assertEqual(resp.prompt_tokens, 1878)
        self.assertEqual(resp.completion_tokens, 131)
        self.assertEqual(resp.total_tokens, 2898)
        self.assertEqual(resp.finish_reason, "max_tokens")
        self.assertEqual(resp.metadata.get("thoughts_tokens"), 889)
        self.assertEqual(resp.metadata.get("total_output_tokens"), 1020)
        self.assertEqual(resp.metadata.get("max_output_tokens"), 1024)

        data = resp.to_dict()
        self.assertEqual(data["metadata"]["thoughts_tokens"], 889)
        self.assertEqual(data["metadata"]["total_output_tokens"], 1020)
        self.assertEqual(data["metadata"]["max_output_tokens"], 1024)


class TaskAwareGenerationPolicyTestCase(SimpleTestCase):
    """Tests for Phase 3 task-aware generation policies, overrides, and propagation."""

    def test_task_generation_policies_defaults(self):
        from pwanimate.ai.gateway import (
            DEFAULT_GENERATION_POLICY,
            get_task_policy,
        )

        self.assertEqual(get_task_policy("conversational").max_output_tokens, 512)
        self.assertEqual(get_task_policy("general").max_output_tokens, 512)
        self.assertEqual(get_task_policy("tool").max_output_tokens, 1024)
        self.assertEqual(get_task_policy("rag").max_output_tokens, 4096)
        # Safe fallback for unknown task
        self.assertEqual(get_task_policy("unknown_flow").max_output_tokens, 1024)
        self.assertEqual(get_task_policy("").max_output_tokens, DEFAULT_GENERATION_POLICY.max_output_tokens)

    def test_orchestrator_budget_resolution_defaults(self):
        from pwanimate.orchestrator.service import PwanimateOrchestrator
        from pwanimate.orchestrator.types import OrchestrationRequest

        orchestrator = PwanimateOrchestrator(
            retrieval_service=MagicMock(),
            gateway=MagicMock(),
        )

        # Request without explicit max_tokens (defaults to None)
        req = OrchestrationRequest(query="What is DNS?")
        self.assertIsNone(req.max_tokens)
        self.assertEqual(orchestrator._resolve_effective_budget(req, "conversational"), 512)
        self.assertEqual(orchestrator._resolve_effective_budget(req, "general"), 512)
        self.assertEqual(orchestrator._resolve_effective_budget(req, "tool"), 1024)
        self.assertEqual(orchestrator._resolve_effective_budget(req, "rag"), 4096)

    def test_orchestrator_budget_resolution_explicit_override(self):
        from pwanimate.orchestrator.service import PwanimateOrchestrator
        from pwanimate.orchestrator.types import OrchestrationRequest

        orchestrator = PwanimateOrchestrator(
            retrieval_service=MagicMock(),
            gateway=MagicMock(),
        )

        # Explicit request max_tokens takes strict precedence
        req = OrchestrationRequest(query="Explain TCP", max_tokens=2048)
        self.assertEqual(req.max_tokens, 2048)
        self.assertEqual(orchestrator._resolve_effective_budget(req, "conversational"), 2048)
        self.assertEqual(orchestrator._resolve_effective_budget(req, "tool"), 2048)
        self.assertEqual(orchestrator._resolve_effective_budget(req, "rag"), 2048)

    @patch("requests.Session.post")
    def test_provider_propagation_gemini_payloads(self, mock_post):
        """Verify conversational=512, tool=1024, rag=4096 reach Gemini maxOutputTokens."""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "candidates": [
                {
                    "content": {"parts": [{"text": "Answer text"}], "role": "model"},
                    "finishReason": "STOP",
                }
            ],
            "usageMetadata": {"promptTokenCount": 50, "candidatesTokenCount": 20, "totalTokenCount": 70},
        }
        mock_post.return_value = mock_resp

        provider = GeminiLLMProvider(api_key="test-key", model_name="gemini-3.6-flash")

        # 1. Conversational tier: 512
        req_conv = LLMRequest(messages=[ChatMessage(role="user", content="Hi")], max_tokens=512)
        provider.generate(req_conv)
        payload_conv = mock_post.call_args[1]["json"]
        self.assertEqual(payload_conv["generationConfig"]["maxOutputTokens"], 512)

        # 2. Tool tier: 1024
        req_tool = LLMRequest(messages=[ChatMessage(role="user", content="Lookup")], max_tokens=1024)
        provider.generate(req_tool)
        payload_tool = mock_post.call_args[1]["json"]
        self.assertEqual(payload_tool["generationConfig"]["maxOutputTokens"], 1024)

        # 3. RAG tier: 4096
        req_rag = LLMRequest(messages=[ChatMessage(role="user", content="Explain subnetting")], max_tokens=4096)
        provider.generate(req_rag)
        payload_rag = mock_post.call_args[1]["json"]
        self.assertEqual(payload_rag["generationConfig"]["maxOutputTokens"], 4096)

    @patch("requests.Session.post")
    def test_groq_and_openrouter_finish_reason_normalization(self, mock_post):
        """Verify length is normalized to max_tokens across Groq and OpenRouter."""
        # Groq normalization test
        mock_groq_resp = MagicMock()
        mock_groq_resp.status_code = 200
        mock_groq_resp.json.return_value = {
            "id": "chatcmpl-groq",
            "choices": [
                {
                    "message": {"content": "Truncated answer"},
                    "finish_reason": "length",
                }
            ],
            "usage": {"prompt_tokens": 100, "completion_tokens": 1024, "total_tokens": 1124},
        }
        mock_post.return_value = mock_groq_resp

        groq_provider = GroqLLMProvider(api_key="test-groq-key")
        groq_resp = groq_provider.generate(LLMRequest(messages=[ChatMessage(role="user", content="Hi")], max_tokens=1024))
        self.assertEqual(groq_resp.finish_reason, "max_tokens")
        self.assertEqual(groq_resp.metadata["provider_finish_reason"], "length")

        # OpenRouter normalization test
        mock_or_resp = MagicMock()
        mock_or_resp.status_code = 200
        mock_or_resp.json.return_value = {
            "id": "gen-or",
            "choices": [
                {
                    "message": {"content": "Truncated answer"},
                    "finish_reason": "length",
                }
            ],
            "usage": {"prompt_tokens": 100, "completion_tokens": 1024, "total_tokens": 1124},
        }
        mock_post.return_value = mock_or_resp

        or_provider = OpenRouterLLMProvider(api_key="test-or-key")
        or_resp = or_provider.generate(LLMRequest(messages=[ChatMessage(role="user", content="Hi")], max_tokens=1024))
        self.assertEqual(or_resp.finish_reason, "max_tokens")
        self.assertEqual(or_resp.metadata["provider_finish_reason"], "length")

    @patch("requests.Session.post")
    def test_rag_generation_under_4096_budget(self, mock_post):
        """Verify RAG request with 4096 ceiling delivers output without exhaustion."""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "candidates": [
                {
                    "content": {
                        "parts": [{"text": "Complete and exhaustive explanation of normalization..."}],
                        "role": "model",
                    },
                    "finishReason": "STOP",
                }
            ],
            "usageMetadata": {
                "promptTokenCount": 1878,
                "candidatesTokenCount": 850,
                "thoughtsTokenCount": 889,
                "totalTokenCount": 3617,
            },
        }
        mock_post.return_value = mock_resp

        provider = GeminiLLMProvider(api_key="test-gemini-key", model_name="gemini-3.6-flash")
        req = LLMRequest(
            task="rag",
            messages=[ChatMessage(role="user", content="Explain normalization in detail")],
            max_tokens=4096,
        )
        resp = provider.generate(req)

        # Verification of payload sent to Gemini
        call_args = mock_post.call_args
        self.assertEqual(call_args[1]["json"]["generationConfig"]["maxOutputTokens"], 4096)

        # Verification of usage and finish reason
        self.assertEqual(resp.finish_reason, "stop")
        self.assertEqual(resp.prompt_tokens, 1878)
        self.assertEqual(resp.completion_tokens, 850)
        self.assertEqual(resp.total_tokens, 3617)
        self.assertEqual(resp.metadata["thoughts_tokens"], 889)
        self.assertEqual(resp.metadata["total_output_tokens"], 1739)
        self.assertEqual(resp.metadata["max_output_tokens"], 4096)
        self.assertEqual(resp.metadata["provider_finish_reason"], "STOP")
        # 1739 total output is comfortably within the 4096 ceiling
        self.assertLess(resp.metadata["total_output_tokens"], resp.metadata["max_output_tokens"])

    def test_gateway_falls_back_when_candidate_returns_empty_content(self):
        """Verify Gateway detects empty content from candidate and falls back to next candidate."""
        from pwanimate.ai.gateway.base import BaseLLMProvider

        class EmptyProvider(BaseLLMProvider):
            provider_name = "mock_empty"
            def is_available(self):
                return True
            def generate(self, request: LLMRequest) -> LLMResponse:
                return LLMResponse(
                    content="",  # Empty content!
                    provider=self.provider_name,
                    model="empty-model",
                    finish_reason="length",
                )

        class GoodProvider(BaseLLMProvider):
            provider_name = "mock_good"
            def is_available(self):
                return True
            def generate(self, request: LLMRequest) -> LLMResponse:
                return LLMResponse(
                    content="Good fallback response.",
                    provider=self.provider_name,
                    model="good-model",
                    finish_reason="stop",
                )

        router = LLMRouter(
            providers={"empty": EmptyProvider(), "good": GoodProvider()},
            default_provider="empty",
        )
        router.resolve_fallback_chain = lambda req, quota_tracker=None: [
            {"provider": "empty", "model": "empty-model"},
            {"provider": "good", "model": "good-model"},
        ]

        gateway = AIGateway(router=router)
        req = LLMRequest(messages=[ChatMessage(role="user", content="Hi")])
        resp = gateway.generate(req)

        self.assertEqual(resp.content, "Good fallback response.")
        self.assertEqual(resp.provider, "mock_good")
        self.assertTrue(resp.metadata.get("fallback_used"))
        self.assertEqual(resp.metadata.get("original_provider"), "empty")

    def test_llm_request_none_max_tokens_defaults_to_1024(self):
        req = LLMRequest(max_tokens=None)
        self.assertEqual(req.max_tokens, 1024)

    def test_orchestration_request_none_max_tokens_defaults_to_none(self):
        from pwanimate.orchestrator.types import OrchestrationRequest
        req = OrchestrationRequest(query="Test query", max_tokens=None)
        self.assertIsNone(req.max_tokens)



