"""
Groq Cloud LLM Provider for Pwanimate.

Implements generative LLM calls against Groq's OpenAI-compatible
chat/completions REST API using direct HTTP requests.
"""

from typing import Any, Dict, List, Optional
import base64
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

GROQ_CHAT_URL = "https://api.groq.com/openai/v1/chat/completions"
GROQ_VISION_MODELS = {"qwen/qwen3.8-27b"}


class GroqLLMProvider(BaseLLMProvider):
    """
    Direct REST provider for Groq Cloud models (e.g., llama-3.3-70b-versatile).
    """

    provider_name: str = "groq"

    def __init__(
        self,
        api_key: Optional[str] = None,
        model_name: Optional[str] = None,
        timeout: Optional[float] = None,
        connect_timeout: Optional[float] = None,
        session: Optional[requests.Session] = None,
    ):
        self.api_key = api_key if api_key is not None else getattr(settings, "PWANIMATE_GROQ_API_KEY", "")
        self.model_name = model_name or getattr(settings, "PWANIMATE_GROQ_MODEL", "openai/gpt-oss-120b")
        self.timeout = timeout if timeout is not None else getattr(settings, "PWANIMATE_LLM_TIMEOUT", 30.0)
        self.connect_timeout = (
            connect_timeout if connect_timeout is not None else getattr(settings, "PWANIMATE_LLM_CONNECT_TIMEOUT", 5.0)
        )
        self.session = session or requests.Session()

    def is_available(self) -> bool:
        """Available if API key is non-empty."""
        return bool(self.api_key and self.api_key.strip())

    def generate(self, request: LLMRequest) -> LLMResponse:
        """
        Send generation request to Groq REST API.
        """
        if not self.is_available():
            raise AIProviderConfigurationError(
                "Groq API key is not configured.",
                provider=self.provider_name,
            )

        model = request.model or self.model_name

        # Only the configured Qwen vision models accept image attachments.
        has_images = any(
            getattr(a, "attachment_type", "") == "image"
            for a in (getattr(request, "attachments", []) or [])
        ) or any(
            any(getattr(a, "attachment_type", "") == "image" for a in (getattr(msg, "attachments", []) or []))
            for msg in request.messages
        )
        if has_images and model.strip().lower() not in GROQ_VISION_MODELS:
            raise AIProviderConfigurationError(
                f"Selected AI model '{model}' on provider '{self.provider_name}' does not support image attachments.",
                provider=self.provider_name,
            )

        payload = self._build_payload(request, model)
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
        }

        try:
            resp = self.session.post(
                GROQ_CHAT_URL,
                json=payload,
                headers=headers,
                timeout=(self.connect_timeout, self.timeout),
            )
        except requests.exceptions.Timeout as exc:
            raise AIProviderTimeoutError(
                f"Groq API request timed out after {self.timeout}s: {exc}",
                provider=self.provider_name,
            ) from exc
        except requests.exceptions.RequestException as exc:
            raise AIProviderAPIError(
                f"Groq HTTP connection error: {exc}",
                provider=self.provider_name,
            ) from exc

        # Handle HTTP status codes
        if resp.status_code == 401 or resp.status_code == 403:
            raise AIProviderAuthenticationError(
                "Groq API authentication failed (invalid or forbidden key).",
                provider=self.provider_name,
                status_code=resp.status_code,
            )
        elif resp.status_code == 429:
            raise AIProviderRateLimitError(
                "Groq API rate limit or quota exceeded.",
                provider=self.provider_name,
                status_code=resp.status_code,
            )
        elif resp.status_code != 200:
            err_msg = self._extract_error_message(resp)
            raise AIProviderAPIError(
                f"Groq API error ({resp.status_code}): {err_msg}",
                provider=self.provider_name,
                status_code=resp.status_code,
            )

        try:
            data = resp.json()
        except Exception as exc:
            raise AIProviderAPIError(
                f"Malformed JSON in Groq response: {exc}",
                provider=self.provider_name,
            ) from exc

        return self._parse_response(data, request, model)

    def generate_stream(self, request: LLMRequest, on_token) -> LLMResponse:
        """
        Streaming variant of generate(): visible text deltas are pushed to
        `on_token` as Groq's SSE stream arrives. Returns the same normalized
        LLMResponse shape (usage/finish captured from the stream tail) so
        callers see identical semantics to the non-streaming path.
        """
        if not self.is_available():
            raise AIProviderConfigurationError(
                "Groq API key is not configured.",
                provider=self.provider_name,
            )

        model = request.model or self.model_name

        # Same image-capability gate as generate(): only Qwen vision models
        # accept image attachments on Groq.
        has_images = any(
            getattr(a, "attachment_type", "") == "image"
            for a in (getattr(request, "attachments", []) or [])
        ) or any(
            any(getattr(a, "attachment_type", "") == "image" for a in (getattr(msg, "attachments", []) or []))
            for msg in request.messages
        )
        if has_images and model.strip().lower() not in GROQ_VISION_MODELS:
            raise AIProviderConfigurationError(
                f"Selected AI model '{model}' on provider '{self.provider_name}' does not support image attachments.",
                provider=self.provider_name,
            )

        payload = self._build_payload(request, model)
        payload["stream"] = True
        payload["stream_options"] = {"include_usage": True}
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
        }

        try:
            resp = self.session.post(
                GROQ_CHAT_URL,
                json=payload,
                headers=headers,
                timeout=(self.connect_timeout, self.timeout),
                stream=True,
            )
        except requests.exceptions.Timeout as exc:
            raise AIProviderTimeoutError(
                f"Groq API request timed out after {self.timeout}s: {exc}",
                provider=self.provider_name,
            ) from exc
        except requests.exceptions.RequestException as exc:
            raise AIProviderAPIError(
                f"Groq HTTP connection error: {exc}",
                provider=self.provider_name,
            ) from exc

        if resp.status_code == 401 or resp.status_code == 403:
            raise AIProviderAuthenticationError(
                "Groq API authentication failed (invalid or forbidden key).",
                provider=self.provider_name,
                status_code=resp.status_code,
            )
        elif resp.status_code == 429:
            raise AIProviderRateLimitError(
                "Groq API rate limit or quota exceeded.",
                provider=self.provider_name,
                status_code=resp.status_code,
            )
        elif resp.status_code != 200:
            err_msg = self._extract_error_message(resp)
            raise AIProviderAPIError(
                f"Groq API error ({resp.status_code}): {err_msg}",
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
        if has_images and model.strip().lower() not in GROQ_VISION_MODELS:
            raise AIProviderConfigurationError(
                f"Selected AI model '{model}' on provider '{self.provider_name}' does not support image attachments.",
                provider=self.provider_name,
            )

        messages: List[Dict[str, Any]] = []

        if request.system_instruction:
            messages.append({"role": "system", "content": request.system_instruction})

        for msg in request.messages:
            content: Any = msg.content
            msg_attachments = list(getattr(msg, "attachments", []) or [])
            if msg.role == "user" and getattr(request, "attachments", None):
                seen_ids = {getattr(att, "id", None) for att in msg_attachments}
                msg_attachments.extend(
                    att for att in request.attachments
                    if getattr(att, "id", None) not in seen_ids
                )
            image_attachments = [
                att for att in msg_attachments
                if getattr(att, "attachment_type", "") == "image"
            ]
            if image_attachments:
                content = [{"type": "text", "text": msg.content}]
                for attachment in image_attachments:
                    image_bytes = getattr(attachment, "data_bytes", None)
                    if not image_bytes and getattr(attachment, "file_path", None):
                        try:
                            with open(attachment.file_path, "rb") as image_file:
                                image_bytes = image_file.read()
                        except OSError as exc:
                            raise AIProviderConfigurationError(
                                f"Could not read image attachment '{getattr(attachment, 'name', 'image')}'.",
                                provider=self.provider_name,
                            ) from exc
                    if not image_bytes:
                        raise AIProviderConfigurationError(
                            f"Image attachment '{getattr(attachment, 'name', 'image')}' has no readable content.",
                            provider=self.provider_name,
                        )
                    mime_type = getattr(attachment, "mime_type", "image/jpeg") or "image/jpeg"
                    # Strip EXIF and downscale before the image leaves the
                    # building: it costs less and it does not hand a third
                    # party the student's location.
                    from pwanimate.ai.vision_images import prepare_image_for_vision

                    image_bytes, mime_type = prepare_image_for_vision(image_bytes, mime_type)
                    image_data = base64.b64encode(image_bytes).decode("ascii")
                    content.append({
                        "type": "image_url",
                        "image_url": {"url": f"data:{mime_type};base64,{image_data}"},
                    })
            messages.append({"role": msg.role, "content": content})

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
                # If there's a last user message, prepend context
                if messages and messages[-1]["role"] == "user":
                    last_content = messages[-1]["content"]
                    if isinstance(last_content, list):
                        last_content.insert(0, {"type": "text", "text": context_text})
                    else:
                        messages[-1]["content"] = f"{context_text}\n\n{last_content}"
                else:
                    messages.append({"role": "user", "content": context_text})

        if not messages:
            messages.append({"role": "user", "content": "Hello"})

        return {
            "model": model,
            "messages": messages,
            "temperature": request.temperature,
            "max_tokens": request.max_tokens,
        }

    def _parse_response(self, data: Dict[str, Any], request: LLMRequest, model: str) -> LLMResponse:
        """Extract content, finish reason, and usage from Groq response."""
        choices = data.get("choices", [])
        if not choices:
            raise AIProviderAPIError(
                "Groq response contained no completion choices.",
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

        total_output_tokens = completion_tokens
        if total_output_tokens is None and (completion_tokens or thoughts_tokens):
            total_output_tokens = (completion_tokens or 0) + (thoughts_tokens or 0)

        # Empty content validation and recovery
        if not content:
            if finish_reason == "max_tokens":
                raise AIProviderAPIError(
                    f"Groq model '{model}' reached maximum token limit ({request.max_tokens}) during reasoning without generating visible content.",
                    provider=self.provider_name,
                )
            elif reasoning:
                content = reasoning
            else:
                raise AIProviderAPIError(
                    f"Groq model '{model}' generated empty content (finish_reason={raw_finish}).",
                    provider=self.provider_name,
                )

        citations = list(request.context.citations) if request.context else []

        metadata: Dict[str, Any] = {
            "groq_id": data.get("id"),
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
