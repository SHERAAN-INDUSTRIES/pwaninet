"""
Tests for Build③ token streaming (docs/local-models-design.md §3.2).

Covers:
- provider-level SSE parsing (Groq/OpenRouter/Gemini) reusing _parse_response,
- gateway fallback semantics: silent failover before the first token, truthful
  interruption (partial text, metadata["interrupted"]) mid-stream,
- chunk coalescing of tiny provider deltas,
- providers without native streaming degrade to a single chunk,
- orchestrator._generate swaps generate -> generate_stream when a callback is
  given,
- the SSE chat protocol: status/chunk/response events end to end.
"""

import json
from unittest import mock

from django.test import SimpleTestCase

from pwanimate.ai.exceptions import AIProviderAPIError, AIProviderRateLimitError
from pwanimate.ai.gateway.base import BaseLLMProvider
from pwanimate.ai.gateway.gateway import AIGateway
from pwanimate.ai.gateway.router import LLMRouter
from pwanimate.ai.gateway.types import ChatMessage, LLMRequest, LLMResponse
from pwanimate.ai.providers.gemini import GeminiLLMProvider
from pwanimate.ai.providers.groq import GroqLLMProvider
from pwanimate.ai.providers.mock import MockLLMProvider
from pwanimate.ai.providers.openrouter import OpenRouterLLMProvider
from pwanimate.orchestrator import PwanimateOrchestrator


class FakeStreamResponse:
    """Minimal stand-in for requests.Response in streaming mode. Lines are
    yielded as UTF-8 BYTES exactly like the real requests iter_lines()."""

    def __init__(self, lines, status_code=200):
        self.status_code = status_code
        self._lines = lines

    def iter_lines(self, decode_unicode=True):
        for line in self._lines:
            yield line.encode("utf-8") if isinstance(line, str) else line

    @staticmethod
    def json():
        return {}


def _sse_lines(*payloads):
    lines = []
    for payload in payloads:
        if isinstance(payload, str):
            lines.append(f"data: {payload}")
        else:
            lines.append(f"data: {json.dumps(payload)}")
    return lines


def _stream_request():
    return LLMRequest(
        messages=[ChatMessage(role="user", content="hi")],
        max_tokens=128,
    )


class GroqStreamingTests(SimpleTestCase):

    def test_sse_deltas_and_usage_reassembled(self):
        provider = GroqLLMProvider(api_key="test-key")
        # Multi-byte characters pin the UTF-8 decode fix: requests' header
        # guessing would otherwise mangle these into 'â' garbage.
        response_lines = _sse_lines(
            {"id": "c1", "choices": [{"delta": {"content": "Héllo "}}]},
            {"id": "c1", "choices": [{"delta": {"content": "wörld–ok"}}]},
            {"id": "c1", "choices": [{"delta": {}, "finish_reason": "stop"}],
             "usage": {"prompt_tokens": 10, "completion_tokens": 2, "total_tokens": 12}},
            "[DONE]",
        )
        tokens = []
        with mock.patch.object(provider, "session") as session:
            session.post.return_value = FakeStreamResponse(response_lines)
            response = provider.generate_stream(_stream_request(), tokens.append)

            payload = session.post.call_args.kwargs["json"]
            self.assertTrue(payload["stream"])
            self.assertEqual(payload["stream_options"], {"include_usage": True})
            self.assertTrue(session.post.call_args.kwargs.get("stream"))

        self.assertEqual(tokens, ["Héllo ", "wörld–ok"])
        self.assertEqual(response.content, "Héllo wörld–ok")
        self.assertEqual(response.total_tokens, 12)
        self.assertEqual(response.finish_reason, "stop")
        self.assertEqual(response.provider, "groq")

    def test_rate_limit_before_first_token_raises(self):
        provider = GroqLLMProvider(api_key="test-key")
        with mock.patch.object(provider, "session") as session:
            session.post.return_value = FakeStreamResponse([], status_code=429)
            with self.assertRaises(AIProviderRateLimitError):
                provider.generate_stream(_stream_request(), lambda _t: None)


class OpenRouterStreamingTests(SimpleTestCase):

    def test_sse_deltas_and_usage_reassembled(self):
        provider = OpenRouterLLMProvider(api_key="test-key")
        response_lines = _sse_lines(
            {"id": "o1", "choices": [{"delta": {"content": "Open "}}]},
            {"id": "o1", "choices": [{"delta": {"content": "Router"}}]},
            {"id": "o1", "choices": [{"delta": {}, "finish_reason": "stop"}],
             "usage": {"prompt_tokens": 4, "completion_tokens": 2, "total_tokens": 6}},
            "[DONE]",
        )
        tokens = []
        with mock.patch.object(provider, "session") as session:
            session.post.return_value = FakeStreamResponse(response_lines)
            response = provider.generate_stream(_stream_request(), tokens.append)

            payload = session.post.call_args.kwargs["json"]
            self.assertTrue(payload["stream"])
            self.assertEqual(payload["usage"], {"include": True})

        self.assertEqual(tokens, ["Open ", "Router"])
        self.assertEqual(response.content, "Open Router")
        self.assertEqual(response.total_tokens, 6)


class GeminiStreamingTests(SimpleTestCase):

    def test_sse_deltas_and_usagemetadata_reassembled(self):
        provider = GeminiLLMProvider(api_key="test-key")
        response_lines = _sse_lines(
            {"candidates": [{"content": {"parts": [{"text": "Gem "}]}}]},
            {"candidates": [{"content": {"parts": [{"text": "ini"}]},
                             "finishReason": "STOP"}],
             "usageMetadata": {"promptTokenCount": 5, "candidatesTokenCount": 2,
                               "totalTokenCount": 7}},
        )
        tokens = []
        with mock.patch.object(provider, "session") as session:
            session.post.return_value = FakeStreamResponse(response_lines)
            response = provider.generate_stream(_stream_request(), tokens.append)

            url = session.post.call_args.args[0]
            self.assertIn(":streamGenerateContent?alt=sse", url)

        self.assertEqual(tokens, ["Gem ", "ini"])
        self.assertEqual(response.content, "Gem ini")
        self.assertEqual(response.total_tokens, 7)


class MockProviderStreamingTests(SimpleTestCase):

    def test_stream_chunks_reassemble_to_full_answer(self):
        provider = MockLLMProvider(default_response_text="A" * 50)
        tokens = []
        response = provider.generate_stream(_stream_request(), tokens.append)
        self.assertEqual("".join(tokens), "A" * 50)
        self.assertEqual(response.content, "A" * 50)


class FakeQuotaTracker:
    """Tracker stand-in for the gateway surface used in streaming."""

    def __init__(self):
        self.rate_limited = set()

    def is_rate_limited(self, provider, model=None):
        return (provider, model) in self.rate_limited

    def record_rate_limit(self, provider, model=None):
        self.rate_limited.add((provider, model))

    def clear_candidate(self, provider, model=None):
        self.rate_limited.discard((provider, model))

    def get_cooldown_remaining(self, provider, model=None):
        return 0.0


class _FakeProvider(BaseLLMProvider):
    """Configurable fake: stream tokens, optionally raise after N tokens."""

    def __init__(self, name, tokens=None, error_after=None, error=None, content=None):
        self.provider_name = name
        self._tokens = list(tokens or [])
        self._error_after = error_after
        self._error = error
        self._content = content

    def is_available(self):
        return True

    def generate(self, request):
        return LLMResponse(
            content=self._content or "",
            provider=self.provider_name,
            model=request.model,
            finish_reason="stop",
        )

    def generate_stream(self, request, on_token):
        if self._error_after is None and self._error is not None:
            raise self._error
        for index, token in enumerate(self._tokens):
            on_token(token)
            if self._error_after is not None and index + 1 >= self._error_after:
                raise self._error
        return LLMResponse(
            content="".join(self._tokens),
            provider=self.provider_name,
            model=request.model,
            finish_reason="stop",
            prompt_tokens=1,
            completion_tokens=max(1, len("".join(self._tokens)) // 4),
            total_tokens=1 + max(1, len("".join(self._tokens)) // 4),
        )


class _DegradedProvider(BaseLLMProvider):
    """Provider without native streaming (legacy/custom/test providers)."""

    def __init__(self, name, content):
        self.provider_name = name
        self._content = content

    def is_available(self):
        return True

    def generate(self, request):
        return LLMResponse(
            content=self._content,
            provider=self.provider_name,
            model=request.model,
            finish_reason="stop",
        )


def _gateway_with(providers, primary):
    router = LLMRouter(providers=providers, default_provider=primary)
    return AIGateway(router=router, quota_tracker=FakeQuotaTracker())


def _chained_request(primary, chain):
    return LLMRequest(
        messages=[ChatMessage(role="user", content="hi")],
        provider=primary,
        model="m0",
        metadata={"fallback_chain": chain},
    )


class GatewayStreamingTests(SimpleTestCase):
    """Fakes register under SUPPORTED_PROVIDERS names (gemini/groq) because
    the router filters candidates against that allow-list."""

    def test_silent_failover_before_first_token(self):
        """A rate-limited primary fails before any token: the user never sees
        an error, the fallback streams, quota_info reports the switch."""
        gateway = _gateway_with(
            {
                "gemini": _FakeProvider(
                    "gemini", error=AIProviderRateLimitError("quota", provider="gemini")
                ),
                "groq": _FakeProvider("groq", tokens=["Hi ", "there"]),
            },
            primary="gemini",
        )
        request = _chained_request("gemini", [
            {"provider": "gemini", "model": "m0"},
            {"provider": "groq", "model": "m1"},
        ])
        tokens = []
        response = gateway.generate_stream(request, tokens.append)

        # Small deltas coalesce: one flush on success.
        self.assertEqual(tokens, ["Hi there"])
        self.assertEqual(response.provider, "groq")
        self.assertEqual(response.content, "Hi there")
        quota = response.metadata["quota_info"]
        self.assertTrue(quota["switched"])
        self.assertEqual(quota["primary_provider"], "gemini")
        self.assertIn({"provider": "gemini", "model": "m0", "reason": "rate_limit"},
                      quota["skipped"])
        self.assertTrue(gateway.quota_tracker.is_rate_limited("gemini", "m0"))

    def test_midstream_failure_returns_partial_truthfully(self):
        """A collapse AFTER tokens reached the user cannot be replayed on a
        fallback: the partial text is returned with interrupted=True."""
        gateway = _gateway_with(
            {
                "gemini": _FakeProvider(
                    "gemini",
                    tokens=["Partial ", "answer"],
                    error_after=2,
                    error=AIProviderAPIError("connection reset", provider="gemini"),
                ),
                "groq": _FakeProvider("groq", tokens=["never ", "used"]),
            },
            primary="gemini",
        )
        request = _chained_request("gemini", [
            {"provider": "gemini", "model": "m0"},
            {"provider": "groq", "model": "m1"},
        ])
        tokens = []
        response = gateway.generate_stream(request, tokens.append)

        # Small deltas coalesce: the interrupted flush emits the partial once.
        self.assertEqual(tokens, ["Partial answer"])
        self.assertEqual(response.content, "Partial answer")
        self.assertEqual(response.finish_reason, "interrupted")
        self.assertTrue(response.metadata["interrupted"])
        self.assertEqual(response.metadata["quota_info"]["active_provider"], "gemini")

    def test_chunk_coalescing_merges_tiny_deltas(self):
        """Provider deltas of a few characters flush as >=24-char chunks."""
        text = "".join(chr(ord("a") + i % 26) for i in range(30))
        gateway = _gateway_with(
            {"gemini": _FakeProvider("gemini", tokens=list(text))},
            primary="gemini",
        )
        request = _chained_request("gemini", [{"provider": "gemini", "model": "m0"}])
        tokens = []
        response = gateway.generate_stream(request, tokens.append)

        self.assertEqual("".join(tokens), text)
        self.assertEqual(response.content, text)
        self.assertEqual(len(tokens), 2, tokens)
        self.assertEqual(len(tokens[0]), 24)

    def test_provider_without_native_streaming_degrades_to_one_chunk(self):
        gateway = _gateway_with(
            {"gemini": _DegradedProvider("gemini", "one shot")},
            primary="gemini",
        )
        request = _chained_request("gemini", [{"provider": "gemini", "model": "m0"}])
        tokens = []
        response = gateway.generate_stream(request, tokens.append)

        self.assertEqual(tokens, ["one shot"])
        self.assertEqual(response.content, "one shot")


class OrchestratorGenerateSwapTests(SimpleTestCase):

    def _orchestrator_with_mock_gateway(self):
        orchestrator = PwanimateOrchestrator.__new__(PwanimateOrchestrator)
        orchestrator.gateway = mock.MagicMock()
        return orchestrator

    def test_generate_streams_when_callback_given(self):
        orchestrator = self._orchestrator_with_mock_gateway()
        request = _stream_request()
        callback = lambda _t: None  # noqa: E731
        orchestrator._generate(request, callback)
        orchestrator.gateway.generate_stream.assert_called_once_with(request, callback)
        orchestrator.gateway.generate.assert_not_called()

    def test_generate_plain_without_callback(self):
        orchestrator = self._orchestrator_with_mock_gateway()
        request = _stream_request()
        orchestrator._generate(request)
        orchestrator.gateway.generate.assert_called_once_with(request)
        orchestrator.gateway.generate_stream.assert_not_called()


class FakeDRFResult:
    """Stand-in for the DRF Response _post_chat returns to the SSE layer."""

    def __init__(self, data, status_code=200):
        self.data = data
        self.status_code = status_code


class SSEProtocolTests(SimpleTestCase):
    """The SSE chat protocol: status, then chunk events carrying the token
    callback text, then the final response event — in order. Runs the view's
    real queue + worker-thread + StreamingHttpResponse machinery with
    _post_chat faked, so no DB or HTTP stack is involved."""

    def test_chunk_events_flow_before_final_response(self):
        from pwanimate.api.views import PwanimateChatView

        view = PwanimateChatView()

        def fake_post_chat(request, progress_callback=None, token_callback=None):
            if progress_callback:
                progress_callback("searching_web")
            if token_callback:
                token_callback("Streamed ")
                token_callback("answer.")
            return FakeDRFResult(
                {"answer": "Streamed answer.", "serving_engine": "Spark Speed"}
            )

        view._post_chat = fake_post_chat
        response = view._stream_chat_response(request=None)

        self.assertEqual(response["Content-Type"], "text/event-stream")
        # The SSE generator is async (served over ASGI in production) —
        # consume it the same way the ASGI handler does.
        from asgiref.sync import async_to_sync

        async def collect():
            return [chunk async for chunk in response.streaming_content]

        body = b"".join(async_to_sync(collect)()).decode("utf-8")

        blocks = [block for block in body.split("\n\n") if block.strip()]
        kinds = [
            next(
                (line[7:] for line in block.splitlines() if line.startswith("event: ")),
                None,
            )
            for block in blocks
        ]
        # Opens with the built-in thinking status, then the progress status,
        # then the chunks, then the final response event.
        self.assertEqual(kinds, ["status", "status", "chunk", "chunk", "response"])

        chunk_texts = [
            json.loads(line[6:])["text"]
            for block, kind in zip(blocks, kinds)
            if kind == "chunk"
            for line in block.splitlines()
            if line.startswith("data: ")
        ]
        self.assertEqual("".join(chunk_texts), "Streamed answer.")
        self.assertIn('"serving_engine":"Spark Speed"', blocks[-1])
