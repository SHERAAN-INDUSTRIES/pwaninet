"""
Google Gemini LLM Provider for Pwanimate.

Implements generative LLM calls against the Google Generative Language
generateContent REST API using direct HTTP requests.
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

GEMINI_GENERATE_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
GEMINI_STREAM_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:streamGenerateContent?alt=sse"


class GeminiLLMProvider(BaseLLMProvider):
    """
    Direct REST provider for Google Gemini models (e.g., gemini-3.6-flash).
    """

    provider_name: str = "gemini"

    def __init__(
        self,
        api_key: Optional[str] = None,
        model_name: Optional[str] = None,
        timeout: Optional[float] = None,
        connect_timeout: Optional[float] = None,
        session: Optional[requests.Session] = None,
    ):
        self.api_key = api_key if api_key is not None else getattr(settings, "PWANIMATE_GEMINI_API_KEY", "")
        self.model_name = model_name or getattr(settings, "PWANIMATE_GEMINI_MODEL", "gemini-3.8-flash")
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
        Send generation request to Gemini REST API.
        """
        if not self.is_available():
            raise AIProviderConfigurationError(
                "Gemini API key is not configured.",
                provider=self.provider_name,
            )

        model = request.model or self.model_name
        if model in {"gemini-2.5-flash", "gemini-1.5-flash", "gemini-2.5-pro", "gemini-1.5-pro"}:
            model = "gemini-3.8-flash"
        url = GEMINI_GENERATE_URL.format(model=model)

        # Build payload
        payload = self._build_payload(request)
        headers = {
            "Content-Type": "application/json",
            "x-goog-api-key": self.api_key,
        }

        try:
            resp = self.session.post(
                url,
                json=payload,
                headers=headers,
                timeout=(self.connect_timeout, self.timeout),
            )
        except requests.exceptions.Timeout as exc:
            raise AIProviderTimeoutError(
                f"Gemini API request timed out after {self.timeout}s: {exc}",
                provider=self.provider_name,
            ) from exc
        except requests.exceptions.RequestException as exc:
            raise AIProviderAPIError(
                f"Gemini HTTP connection error: {exc}",
                provider=self.provider_name,
            ) from exc

        # Handle HTTP status codes
        if resp.status_code == 401 or resp.status_code == 403:
            raise AIProviderAuthenticationError(
                "Gemini API authentication failed (invalid or forbidden key).",
                provider=self.provider_name,
                status_code=resp.status_code,
            )
        elif resp.status_code == 429:
            err_msg = self._extract_error_message(resp)
            raise AIProviderRateLimitError(
                f"Gemini API rate limit or quota exceeded: {err_msg}",
                provider=self.provider_name,
                status_code=resp.status_code,
            )
        elif resp.status_code != 200:
            err_msg = self._extract_error_message(resp)
            raise AIProviderAPIError(
                f"Gemini API error ({resp.status_code}): {err_msg}",
                provider=self.provider_name,
                status_code=resp.status_code,
            )

        try:
            data = resp.json()
        except Exception as exc:
            raise AIProviderAPIError(
                f"Malformed JSON in Gemini response: {exc}",
                provider=self.provider_name,
            ) from exc

        return self._parse_response(data, request, model)

    def generate_stream(self, request: LLMRequest, on_token) -> LLMResponse:
        """
        Streaming variant of generate(): text deltas from Gemini's SSE
        streamGenerateContent endpoint are pushed to `on_token`. Returns the
        same normalized LLMResponse shape (usageMetadata from the stream
        tail) so callers see identical semantics to the non-streaming path.
        """
        if not self.is_available():
            raise AIProviderConfigurationError(
                "Gemini API key is not configured.",
                provider=self.provider_name,
            )

        model = request.model or self.model_name
        if model in {"gemini-2.5-flash", "gemini-1.5-flash", "gemini-2.5-pro", "gemini-1.5-pro"}:
            model = "gemini-3.8-flash"
        url = GEMINI_STREAM_URL.format(model=model)

        payload = self._build_payload(request)
        headers = {
            "Content-Type": "application/json",
            "x-goog-api-key": self.api_key,
        }

        try:
            resp = self.session.post(
                url,
                json=payload,
                headers=headers,
                timeout=(self.connect_timeout, self.timeout),
                stream=True,
            )
        except requests.exceptions.Timeout as exc:
            raise AIProviderTimeoutError(
                f"Gemini API request timed out after {self.timeout}s: {exc}",
                provider=self.provider_name,
            ) from exc
        except requests.exceptions.RequestException as exc:
            raise AIProviderAPIError(
                f"Gemini HTTP connection error: {exc}",
                provider=self.provider_name,
            ) from exc

        if resp.status_code == 401 or resp.status_code == 403:
            raise AIProviderAuthenticationError(
                "Gemini API authentication failed (invalid or forbidden key).",
                provider=self.provider_name,
                status_code=resp.status_code,
            )
        elif resp.status_code == 429:
            err_msg = self._extract_error_message(resp)
            raise AIProviderRateLimitError(
                f"Gemini API rate limit or quota exceeded: {err_msg}",
                provider=self.provider_name,
                status_code=resp.status_code,
            )
        elif resp.status_code != 200:
            err_msg = self._extract_error_message(resp)
            raise AIProviderAPIError(
                f"Gemini API error ({resp.status_code}): {err_msg}",
                provider=self.provider_name,
                status_code=resp.status_code,
            )

        text_parts: List[str] = []
        finish_reason = None
        usage: Dict[str, Any] = {}

        for raw_line in resp.iter_lines():
            if isinstance(raw_line, bytes):
                # SSE bytes are UTF-8 by spec; never trust requests' header
                # guessing (text/event-stream without charset falls back to
                # Latin-1 and mangles multi-byte characters into 'â' garbage).
                raw_line = raw_line.decode("utf-8", errors="replace")
            if not raw_line or not raw_line.startswith("data:"):
                continue
            data_str = raw_line[5:].strip()
            if not data_str:
                continue
            try:
                chunk = json.loads(data_str)
            except ValueError:
                continue
            if chunk.get("usageMetadata"):
                usage = chunk["usageMetadata"]
            candidates = chunk.get("candidates") or []
            if not candidates:
                continue
            candidate = candidates[0]
            for part in (candidate.get("content") or {}).get("parts") or []:
                piece = part.get("text")
                if piece:
                    text_parts.append(piece)
                    on_token(piece)
            if candidate.get("finishReason"):
                finish_reason = candidate["finishReason"]

        # Reassemble a Gemini-shaped payload and reuse _parse_response for
        # finish handling and usageMetadata extraction.
        synthetic = {
            "candidates": [{
                "content": {"parts": [{"text": "".join(text_parts)}]},
                "finishReason": finish_reason or "STOP",
            }],
            "usageMetadata": usage,
        }
        return self._parse_response(synthetic, request, model)

    def _build_payload(self, request: LLMRequest) -> Dict[str, Any]:
        """Construct Gemini generateContent JSON payload."""
        system_instruction_text = request.system_instruction or ""
        contents: List[Dict[str, Any]] = []

        # Process messages
        for idx, msg in enumerate(request.messages):
            if msg.role == "system":
                # System message in turn list gets appended to system instructions
                if system_instruction_text:
                    system_instruction_text += "\n" + msg.content
                else:
                    system_instruction_text = msg.content
            elif msg.role == "assistant":
                contents.append({
                    "role": "model",
                    "parts": [{"text": msg.content}],
                })
            else:  # user
                parts = [{"text": msg.content}]

                # Check for attachments on this message or request level
                msg_attachments = list(getattr(msg, "attachments", []) or [])
                if idx == len(request.messages) - 1 and getattr(request, "attachments", None):
                    seen_ids = {getattr(a, "id", None) for a in msg_attachments}
                    for req_att in request.attachments:
                        req_id = getattr(req_att, "id", None)
                        if req_id is None or req_id not in seen_ids:
                            msg_attachments.append(req_att)
                            if req_id is not None:
                                seen_ids.add(req_id)

                for att in msg_attachments:
                    att_type = getattr(att, "attachment_type", "document")
                    att_bytes = getattr(att, "data_bytes", None)
                    if not att_bytes and getattr(att, "file_path", None):
                        try:
                            with open(att.file_path, "rb") as f:
                                att_bytes = f.read()
                        except Exception as e:
                            logger.warning("Could not read file_path for image attachment: %s", e)
                    att_mime = getattr(att, "mime_type", "image/jpeg")
                    if att_type == "image" and att_bytes:
                        # Strip EXIF and downscale before the image leaves
                        # the building: it costs less and it does not hand a
                        # third party the student's location.
                        from pwanimate.ai.vision_images import prepare_image_for_vision

                        att_bytes, att_mime = prepare_image_for_vision(att_bytes, att_mime)
                        b64_data = base64.b64encode(att_bytes).decode("utf-8")
                        parts.append({
                            "inlineData": {
                                "mimeType": att_mime,
                                "data": b64_data,
                            }
                        })

                contents.append({
                    "role": "user",
                    "parts": parts,
                })

        # Inject context into contents if present
        if request.context and (
            request.context.items or
            request.context.explicit_resources or
            request.context.retrieved_context or
            getattr(request.context, "user_context", None) or
            getattr(request.context, "student_context", None)
        ):
            context_text = request.context.format_context_text()
            if context_text:
                # If there's a last user message, prepend context parts
                if contents and contents[-1]["role"] == "user":
                    contents[-1]["parts"].insert(0, {"text": context_text})
                else:
                    # Add context as a user part
                    contents.append({
                        "role": "user",
                        "parts": [{"text": context_text}],
                    })

        # Fallback if no messages were provided
        if not contents:
            contents.append({
                "role": "user",
                "parts": [{"text": "Hello"}],
            })

        payload: Dict[str, Any] = {
            "contents": contents,
            "generationConfig": {
                "temperature": request.temperature,
                "maxOutputTokens": request.max_tokens,
            },
        }

        if system_instruction_text:
            payload["systemInstruction"] = {
                "parts": [{"text": system_instruction_text}]
            }

        return payload

    def _parse_response(self, data: Dict[str, Any], request: LLMRequest, model: str) -> LLMResponse:
        """Extract text, finish reason, and usage from response JSON."""
        candidates = data.get("candidates", [])
        if not candidates:
            raise AIProviderAPIError(
                "Gemini response contained no candidate outputs.",
                provider=self.provider_name,
                details=data,
            )

        candidate = candidates[0]
        content_parts = candidate.get("content", {}).get("parts", [])
        text = "".join(part.get("text", "") for part in content_parts)
        finish_reason = candidate.get("finishReason", "stop").lower()

        usage = data.get("usageMetadata", {})
        prompt_tokens = usage.get("promptTokenCount")
        completion_tokens = usage.get("candidatesTokenCount")
        total_tokens = usage.get("totalTokenCount")
        thoughts_tokens = usage.get("thoughtsTokenCount")

        # Total generated output tokens (visible candidate tokens + reasoning/thinking tokens)
        total_output_tokens = None
        if completion_tokens is not None or thoughts_tokens is not None:
            total_output_tokens = (completion_tokens or 0) + (thoughts_tokens or 0)

        logger.debug(
            "Gemini usageMetadata: prompt=%s candidates=%s thoughts=%s total=%s max_output=%s finish=%s",
            prompt_tokens,
            completion_tokens,
            thoughts_tokens,
            total_tokens,
            request.max_tokens,
            candidate.get("finishReason"),
        )

        citations = list(request.context.citations) if request.context else []

        metadata: Dict[str, Any] = {
            "gemini_finish_reason": candidate.get("finishReason"),
            "provider_finish_reason": candidate.get("finishReason"),
            "max_output_tokens": request.max_tokens,
            "thoughts_tokens": thoughts_tokens,
            "total_output_tokens": total_output_tokens,
        }

        return LLMResponse(
            content=text,
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
