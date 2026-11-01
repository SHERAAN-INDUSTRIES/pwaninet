"""
OpenRouter LLM Provider for Pwanimate.

Implements generative LLM calls against OpenRouter's OpenAI-compatible
chat/completions REST API using direct HTTP requests.
"""

from typing import Any, Dict, List, Optional
import json
import logging
import requests

from django.conf import settings

from pwanimate.ai.exceptions import (
    AIProviderAPIError,
    AIProviderAuthenticationError,
    AIProviderConfigurationError,
    AIProviderRateLimitError,
    AIProviderTimeoutError,
)
from pwanimate.ai.gateway.base import BaseLLMProvider
from pwanimate.ai.gateway.types import ChatMessage, LLMRequest, LLMResponse

logger = logging.getLogger(__name__)

OPENROUTER_CHAT_URL = "https://openrouter.ai/api/v1/chat/completions"


class OpenRouterLLMProvider(BaseLLMProvider):
    """
    Direct REST provider for OpenRouter API.
    """

    provider_name: str = "openrouter"

    def __init__(
        self,
        api_key: Optional[str] = None,
        model_name: Optional[str] = None,
        timeout: Optional[float] = None,
        connect_timeout: Optional[float] = None,
        site_url: str = "https://vasto.local",
        site_name: str = "Pwanimate",
        session: Optional[requests.Session] = None,
    ):
        self.api_key = api_key if api_key is not None else getattr(settings, "PWANIMATE_OPENROUTER_API_KEY", "")
        self.model_name = model_name or getattr(settings, "PWANIMATE_OPENROUTER_MODEL", "nvidia/nemotron-3-ultra-550b-a55b:free")
        self.timeout = timeout if timeout is not None else getattr(settings, "PWANIMATE_LLM_TIMEOUT", 30.0)
        self.connect_timeout = (
            connect_timeout if connect_timeout is not None else getattr(settings, "PWANIMATE_LLM_CONNECT_TIMEOUT", 5.0)
        )
        self.site_url = site_url
        self.site_name = site_name
        self.session = session or requests.Session()

    def is_available(self) -> bool:
        """Available if API key is non-empty."""
        return bool(self.api_key and self.api_key.strip())

    def generate(self, request: LLMRequest) -> LLMResponse:
        """
        Send generation request to OpenRouter REST API.
        """
        if not self.is_available():
            raise AIProviderConfigurationError(
                "OpenRouter API key is not configured.",
                provider=self.provider_name,
            )

        model = request.model or self.model_name

        # Enforce explicit capability check: OpenRouter free tier text models do not support image attachments
        has_images = any(
            getattr(a, "attachment_type", "") == "image"
            for a in (getattr(request, "attachments", []) or [])
        ) or any(
            any(getattr(a, "attachment_type", "") == "image" for a in (getattr(msg, "attachments", []) or []))
            for msg in request.messages
        )
        if has_images:
            raise AIProviderConfigurationError(
                f"Selected AI model '{model}' on provider '{self.provider_name}' does not support image attachments. "
                "Please select Google Gemini for multimodal image analysis.",
                provider=self.provider_name,
            )

        payload = self._build_payload(request, model)
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
            "HTTP-Referer": self.site_url,
            "X-Title": self.site_name,
        }

        try:
            resp = self.session.post(
                OPENROUTER_CHAT_URL,
                json=payload,
                headers=headers,
                timeout=(self.connect_timeout, self.timeout),
            )
        except requests.exceptions.Timeout as exc:
            raise AIProviderTimeoutError(
                f"OpenRouter API request timed out after {self.timeout}s: {exc}",
                provider=self.provider_name,
            ) from exc
        except requests.exceptions.RequestException as exc:
            raise AIProviderAPIError(
                f"OpenRouter HTTP connection error: {exc}",
                provider=self.provider_name,
            ) from exc

        # Handle HTTP status codes
        if resp.status_code == 401 or resp.status_code == 403:
            raise AIProviderAuthenticationError(
                "OpenRouter API authentication failed (invalid or forbidden key).",
                provider=self.provider_name,
                status_code=resp.status_code,
            )
        elif resp.status_code == 429:
            raise AIProviderRateLimitError(
                "OpenRouter API rate limit or quota exceeded.",
                provider=self.provider_name,
                status_code=resp.status_code,
            )
        elif resp.status_code != 200:
            err_msg = self._extract_error_message(resp)
            raise AIProviderAPIError(
                f"OpenRouter API error ({resp.status_code}): {err_msg}",
                provider=self.provider_name,
                status_code=resp.status_code,
            )

        try:
            data = resp.json()
        except Exception as exc:
            raise AIProviderAPIError(
                f"Malformed JSON in OpenRouter response: {exc}",
                provider=self.provider_name,
            ) from exc

        return self._parse_response(data, request, model)

    def generate_stream(self, request: LLMRequest, on_token) -> LLMResponse:
        """
        Streaming variant of generate(): visible text deltas are pushed to
        `on_token` as OpenRouter's SSE stream arrives. Returns the same
        normalized LLMResponse shape (usage captured via `usage.include`).
        """
        if not self.is_available():
            raise AIProviderConfigurationError(
                "OpenRouter API key is not configured.",
                provider=self.provider_name,
            )

        model = request.model or self.model_name

        # OpenRouter free-tier text models do not support image attachments
        # (same gate as generate()).
        has_images = any(
            getattr(a, "attachment_type", "") == "image"
            for a in (getattr(request, "attachments", []) or [])
        ) or any(
            any(getattr(a, "attachment_type", "") == "image" for a in (getattr(msg, "attachments", []) or []))
            for msg in request.messages
        )
        if has_images:
            raise AIProviderConfigurationError(
                f"Selected AI model '{model}' on provider '{self.provider_name}' does not support image attachments. "
                "Please select Google Gemini for multimodal image analysis.",
                provider=self.provider_name,
            )

        payload = self._build_payload(request, model)
        payload["stream"] = True
        payload["usage"] = {"include": True}
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
            "HTTP-Referer": self.site_url,
            "X-Title": self.site_name,
        }

        try:
            resp = self.session.post(
                OPENROUTER_CHAT_URL,
                json=payload,
                headers=headers,
                timeout=(self.connect_timeout, self.timeout),
                stream=True,
            )
        except requests.exceptions.Timeout as exc:
            raise AIProviderTimeoutError(
                f"OpenRouter API request timed out after {self.timeout}s: {exc}",
                provider=self.provider_name,
            ) from exc
        except requests.exceptions.RequestException as exc:
            raise AIProviderAPIError(
                f"OpenRouter HTTP connection error: {exc}",
                provider=self.provider_name,
            ) from exc

        if resp.status_code == 401 or resp.status_code == 403:
            raise AIProviderAuthenticationError(
                "OpenRouter API authentication failed (invalid or forbidden key).",
                provider=self.provider_name,
                status_code=resp.status_code,
            )
        elif resp.status_code == 429:
            raise AIProviderRateLimitError(
                "OpenRouter API rate limit or quota exceeded.",
                provider=self.provider_name,
                status_code=resp.status_code,
            )
        elif resp.status_code != 200:
            err_msg = self._extract_error_message(resp)
            raise AIProviderAPIError(
                f"OpenRouter API error ({resp.status_code}): {err_msg}",
                provider=self.provider_name,
                status_code=resp.status_code,
            )

        content_parts: List[str] = []
        reasoning_parts: List[str] = []
        finish_reason = None
        usage: Dict[str, Any] = {}
        stream_id = None

        for raw_line in resp.iter_lines():
            if isinstance(raw_line, bytes):
                # SSE bytes are UTF-8 by spec; never trust requests' header
                # guessing (text/event-stream without charset falls back to
                # Latin-1 and mangles multi-byte characters into 'â' garbage).
                raw_line = raw_line.decode("utf-8", errors="replace")
            if not raw_line or not raw_line.startswith("data:"):
                continue
            data_str = raw_line[5:].strip()
            if not data_str or data_str == "[DONE]":
                continue
            try:
                chunk = json.loads(data_str)
            except ValueError:
                continue
            stream_id = stream_id or chunk.get("id")
            usage_chunk = chunk.get("usage")
            if usage_chunk:
                usage = usage_chunk
            choices = chunk.get("choices") or []
            if not choices:
                continue
            choice = choices[0]
            delta = choice.get("delta") or {}
            piece = delta.get("content")
            if piece:
                content_parts.append(piece)
                on_token(piece)
            reasoning_piece = delta.get("reasoning") or delta.get("reasoning_content")
            if reasoning_piece:
                reasoning_parts.append(reasoning_piece)
            if choice.get("finish_reason"):
                finish_reason = choice["finish_reason"]

        # Reassemble an OpenAI-shaped payload and reuse _parse_response for
        # finish normalization, usage extraction and empty-content recovery.
        synthetic = {
            "id": stream_id,
            "choices": [{
                "message": {
                    "role": "assistant",
                    "content": "".join(content_parts),
                    "reasoning": "".join(reasoning_parts),
                },
                "finish_reason": finish_reason or "stop",
            }],
            "usage": usage,
        }
        return self._parse_response(synthetic, request, model)

    def _build_payload(self, request: LLMRequest, model: str) -> Dict[str, Any]:
        """Construct OpenAI-compatible chat completions payload."""
        has_images = any(
            getattr(a, "attachment_type", "") == "image"
            for a in (getattr(request, "attachments", []) or [])
        ) or any(
            any(getattr(a, "attachment_type", "") == "image" for a in (getattr(msg, "attachments", []) or []))
            for msg in request.messages
        )
        if has_images:
            raise AIProviderConfigurationError(
                f"Selected AI model '{model}' on provider '{self.provider_name}' does not support image attachments. "
                "Please select Google Gemini for multimodal image analysis.",
                provider=self.provider_name,
            )

        messages: List[Dict[str, str]] = []

        if request.system_instruction:
            messages.append({"role": "system", "content": request.system_instruction})

        for msg in request.messages:
            messages.append({"role": msg.role, "content": msg.content})

        # Inject context into messages if present
        if request.context and (
            request.context.items or
            request.context.explicit_resources or
            request.context.retrieved_context or
            getattr(request.context, "user_context", None) or
            getattr(request.context, "student_context", None)
        ):
            context_text = request.context.format_context_text()
            if context_text:
                if messages and messages[-1]["role"] == "user":
                    messages[-1]["content"] = f"{context_text}\n\n{messages[-1]['content']}"
                else:
                    messages.append({"role": "user", "content": context_text})

        if not messages:
            messages.append({"role": "user", "content": "Hello"})

        model_lower = model.lower()
        is_reasoning_model = any(
            pattern in model_lower
            for pattern in ("nex-", "r1", "reasoning", "thinking", "nemotron")
        )

        max_tokens = request.max_tokens or 1500
        if is_reasoning_model and max_tokens <= 1024:
            max_tokens = 2048

        payload: Dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": request.temperature,
            "max_tokens": max_tokens,
        }

        # Configure reasoning effort if supported/requested
        if request.metadata and "reasoning" in request.metadata:
            payload["reasoning"] = request.metadata["reasoning"]
        elif is_reasoning_model:
            payload["reasoning"] = {"effort": "low"}

        return payload

    def _parse_response(self, data: Dict[str, Any], request: LLMRequest, model: str) -> LLMResponse:
        """Extract content, finish reason, and usage from OpenRouter response."""
        choices = data.get("choices", [])
        if not choices:
            raise AIProviderAPIError(
                "OpenRouter response contained no completion choices.",
                provider=self.provider_name,
                details=data,
            )

        choice = choices[0]
        msg = choice.get("message", {})
        content = (msg.get("content") or "").strip()
        reasoning = (msg.get("reasoning") or msg.get("reasoning_content") or "").strip()

        raw_finish = choice.get("finish_reason")
        finish_reason = raw_finish
        if raw_finish:
            lower_finish = raw_finish.strip().lower()
            if lower_finish in ("length", "max_tokens"):
                finish_reason = "max_tokens"
            elif lower_finish == "stop":
                finish_reason = "stop"
            else:
                finish_reason = lower_finish

        usage = data.get("usage", {})
        prompt_tokens = usage.get("prompt_tokens")
        completion_tokens = usage.get("completion_tokens")
        total_tokens = usage.get("total_tokens")

        # Extract reasoning/thinking tokens
        details = usage.get("completion_tokens_details") or {}
        thoughts_tokens = details.get("reasoning_tokens") or usage.get("reasoning_tokens")

        # In OpenRouter/OpenAI standard, completion_tokens includes reasoning_tokens
        total_output_tokens = completion_tokens
        if total_output_tokens is None and (completion_tokens or thoughts_tokens):
            total_output_tokens = (completion_tokens or 0) + (thoughts_tokens or 0)

        # Empty content validation and recovery
        if not content:
            if finish_reason == "max_tokens":
                raise AIProviderAPIError(
                    f"OpenRouter model '{model}' reached maximum token limit ({request.max_tokens}) during reasoning without generating visible content.",
                    provider=self.provider_name,
                )
            elif reasoning:
                # Fall back to reasoning text if model only populated reasoning field on normal stop
                content = reasoning
            else:
                raise AIProviderAPIError(
                    f"OpenRouter model '{model}' generated empty content (finish_reason={raw_finish}).",
                    provider=self.provider_name,
                )

        citations = list(request.context.citations) if request.context else []

        metadata: Dict[str, Any] = {
            "openrouter_id": data.get("id"),
            "provider_finish_reason": raw_finish,
            "max_output_tokens": request.max_tokens,
            "thoughts_tokens": thoughts_tokens,
            "total_output_tokens": total_output_tokens,
        }
        if reasoning:
            metadata["reasoning"] = reasoning

        return LLMResponse(
            content=content,
            provider=self.provider_name,
            model=model,
            finish_reason=finish_reason,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
            citations=citations,
            metadata=metadata,
        )

    def _extract_error_message(self, resp: requests.Response) -> str:
        """Extract readable error message without leaking sensitive parameters."""
        try:
            body = resp.json()
            err = body.get("error", {})
            return err.get("message", resp.text[:200])
        except Exception:
            return resp.text[:200]
