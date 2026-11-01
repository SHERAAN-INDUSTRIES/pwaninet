"""
Cloudflare Workers AI provider.

Self-hosted models are not the only way to own your inference. Cloudflare
Workers AI hosts open model weights and bills per request, with a free daily
allocation that belongs to *your* account rather than to a provider who can
revoke a free key. For the free tier that difference matters more than the
per-request price.

This provider speaks the Workers AI REST API::

    POST https://api.cloudflare.com/client/v4/accounts/{account}/ai/run/{model}

The payload is OpenAI-shaped (``messages`` with ``content`` parts), which is
what the vision models expect. Note that the OpenAI-compatible endpoint
``/ai/v1/chat/completions`` does **not** accept the content-array image
format -- ``/ai/run/`` does. Both facts were verified against the live API.

The token used here (``PWANIMATE_CLOUDFLARE_API_TOKEN``) is AI-scoped: it
can run inference but cannot read zone settings. Keep it that way -- it is
the token that goes in the container, not a zone-wide credential.

Model licences: some Workers AI models require a one-time acceptance before
first use, e.g. Llama 3.2 Vision needs::

    POST /accounts/{account}/ai/run/@cf/meta/llama-3.2-11b-vision-instruct
    {"prompt": "agree"}

That acceptance is a legal act, so it is deliberately *not* done from code.
The operator runs it once; this provider only surfaces the API's message if
it has not happened.
"""

import base64
import json
import logging
import time
from typing import Optional

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
from pwanimate.ai.gateway.types import LLMRequest, LLMResponse

logger = logging.getLogger(__name__)

CLOUDFLARE_AI_BASE = "https://api.cloudflare.com/client/v4/accounts"
DEFAULT_VISION_MODEL = "@cf/meta/llama-3.2-11b-vision-instruct"


class CloudflareLLMProvider(BaseLLMProvider):
    """
    Cloudflare Workers AI.

    Two things this provider does that the others do not:

    * it reports the *neurons* an inference cost, which is what the account
      budget is denominated in;
    * it is the only provider on the free path that does not depend on a
      third party's goodwill.
    """

    provider_name = "cloudflare"

    def __init__(
        self,
        api_token: Optional[str] = None,
        account_id: Optional[str] = None,
        model_name: Optional[str] = None,
        timeout: Optional[int] = None,
    ):
        configured_token = (
            getattr(settings, "PWANIMATE_CLOUDFLARE_API_TOKEN", "")
            or getattr(settings, "CLOUDFLARE_API_TOKEN", "")
        )
        configured_account = (
            getattr(settings, "PWANIMATE_CLOUDFLARE_ACCOUNT_ID", "")
            or getattr(settings, "CLOUDFLARE_ACCOUNT_ID", "")
        )
        self.api_token = (api_token if api_token is not None else configured_token or "").strip()
        self.account_id = (account_id if account_id is not None else configured_account or "").strip()
        self.model_name = (
            model_name
            or getattr(settings, "PWANIMATE_CLOUDFLARE_VISION_MODEL", "")
            or DEFAULT_VISION_MODEL
        ).strip()
        self.timeout = timeout if timeout is not None else int(
            getattr(settings, "PWANIMATE_LLM_TIMEOUT", 30)
        )
        self.connect_timeout = float(getattr(settings, "PWANIMATE_LLM_CONNECT_TIMEOUT", 2.0))

    # ------------------------------------------------------------------ setup
    def is_available(self) -> bool:
        """Local configuration only -- never a network call."""
        return bool(self.api_token and self.account_id and self.model_name)

    def _endpoint(self, model: Optional[str] = None) -> str:
        clean_account = self.account_id.strip()
        clean_model = (model or self.model_name).strip()
        if clean_model.startswith("/"):
            clean_model = clean_model[1:]
        return f"{CLOUDFLARE_AI_BASE}/{clean_account}/ai/run/{clean_model}"

    # --------------------------------------------------------------- payloads
    def _build_payload(self, request: LLMRequest, model: Optional[str] = None) -> dict:
        """OpenAI-shaped messages, with image parts for image attachments."""
        messages = []

        system_text = []
        for msg in request.messages:
            role = getattr(msg, "role", "user")
            content = getattr(msg, "content", "") or ""
            if role == "system":
                system_text.append(content)
                continue
            if role == "assistant":
                messages.append({"role": "assistant", "content": content})
                continue

            parts = [{"type": "text", "text": content}]
            for att in self._image_attachments(request, msg):
                parts.append({
                    "type": "image_url",
                    "image_url": {"url": att},
                })
            messages.append({
                "role": "user",
                "content": parts if len(parts) > 1 else content,
            })

        if system_text:
            messages.insert(0, {"role": "system", "content": "\n\n".join(system_text)})

        if request.context is not None:
            context_text = request.context.format_context_text()
            if context_text:
                for message in reversed(messages):
                    if message.get("role") != "user":
                        continue
                    if isinstance(message.get("content"), list):
                        message["content"].insert(0, {"type": "text", "text": context_text})
                    else:
                        message["content"] = f"{context_text}\n\n{message.get('content', '')}"
                    break

        payload = {
            "messages": messages,
            "max_tokens": int(request.max_tokens or 1024),
            "temperature": float(request.temperature if request.temperature is not None else 0.2),
        }
        return payload

    @staticmethod
    def _image_attachments(request: LLMRequest, msg) -> list:
        """Data URIs for every image attachment on this message or the request."""
        uris = []
        seen = set()

        candidates = list(getattr(msg, "attachments", None) or [])
        candidates.extend(list(getattr(request, "attachments", None) or []))

        for att in candidates:
            att_type = getattr(att, "attachment_type", "")
            if att_type != "image":
                continue
            att_id = str(getattr(att, "id", "") or "")
            if att_id and att_id in seen:
                continue
            if att_id:
                seen.add(att_id)

            data_bytes = getattr(att, "data_bytes", None)
            if not data_bytes and getattr(att, "file_path", None):
                try:
                    with open(att.file_path, "rb") as handle:
                        data_bytes = handle.read()
                except OSError as exc:
                    raise AIProviderConfigurationError(
                        f"Could not read image attachment '{getattr(att, 'name', 'image')}'.",
                        provider=CloudflareLLMProvider.provider_name,
                    ) from exc
            if not data_bytes:
                continue

            from pwanimate.ai.vision_images import prepare_image_for_vision

            mime_type = getattr(att, "mime_type", "image/jpeg") or "image/jpeg"
            data_bytes, mime_type = prepare_image_for_vision(data_bytes, mime_type)
            encoded = base64.b64encode(data_bytes).decode("ascii")
            uris.append(f"data:{mime_type};base64,{encoded}")
        return uris

    # --------------------------------------------------------------- requests
    def generate(self, request: LLMRequest) -> LLMResponse:
        if not self.is_available():
            raise AIProviderConfigurationError(
                "Cloudflare Workers AI is not configured. Set PWANIMATE_CLOUDFLARE_API_TOKEN "
                "and PWANIMATE_CLOUDFLARE_ACCOUNT_ID.",
                provider=self.provider_name,
            )

        model = request.model or self.model_name
        endpoint = self._endpoint(model)
        payload = self._build_payload(request, model=model)
        headers = {
            "Authorization": f"Bearer {self.api_token}",
            "Content-Type": "application/json",
        }

        logger.info(
            "[CLOUDFLARE-AI] Requesting %s (model=%s)", request.task, model
        )
        started = time.monotonic()
        try:
            response = requests.post(
                endpoint,
                json=payload,
                headers=headers,
                timeout=(self.connect_timeout, self.timeout),
            )
        except requests.exceptions.Timeout as exc:
            raise AIProviderTimeoutError(
                f"Cloudflare Workers AI request timed out after {self.timeout}s: {exc}",
                provider=self.provider_name,
            ) from exc
        except requests.exceptions.RequestException as exc:
            raise AIProviderAPIError(
                f"Cloudflare Workers AI network error: {exc}",
                provider=self.provider_name,
            ) from exc

        duration = time.monotonic() - started
        return self._parse_response(response, duration)

    def _parse_response(self, response, duration: float) -> LLMResponse:
        if response.status_code in (401, 403):
            message = response.text[:400]
            if "Model Agreement" in message or "agree" in message.lower():
                message = (
                    "This model needs its licence accepted once. An operator must POST "
                    '{"prompt": "agree"} to '
                    f"{self._endpoint()} before it can be used."
                )
            raise AIProviderAuthenticationError(
                f"Cloudflare Workers AI authentication failed (status {response.status_code}). {message}",
                provider=self.provider_name,
                status_code=response.status_code,
            )

        if response.status_code == 429:
            retry_after = response.headers.get("Retry-After", "not provided")
            raise AIProviderRateLimitError(
                f"Cloudflare Workers AI quota exhausted (429). Retry-After: {retry_after}.",
                provider=self.provider_name,
                status_code=429,
                details={"retry_after": retry_after},
            )

        if response.status_code >= 500:
            raise AIProviderAPIError(
                f"Cloudflare Workers AI server error (status {response.status_code}): {response.text[:300]}",
                provider=self.provider_name,
                status_code=response.status_code,
            )

        if response.status_code != 200:
            raise AIProviderAPIError(
                f"Cloudflare Workers AI unexpected status {response.status_code}: {response.text[:300]}",
                provider=self.provider_name,
                status_code=response.status_code,
            )

        try:
            body = response.json()
        except ValueError as exc:
            raise AIProviderAPIError(
                f"Cloudflare Workers AI returned malformed JSON: {exc}",
                provider=self.provider_name,
            ) from exc

        if not body.get("success", False):
            errors = body.get("errors", [])
            detail = "; ".join(
                f"[{e.get('code', '?')}] {e.get('message', '')}" for e in errors
            ) or response.text[:300]
            raise AIProviderAPIError(
                f"Cloudflare Workers AI returned success=false: {detail}",
                provider=self.provider_name,
                details={"errors": errors},
            )

        result = body.get("result")
        if not isinstance(result, dict):
            raise AIProviderAPIError(
                f"Cloudflare Workers AI response missing 'result': {list(body.keys())}",
                provider=self.provider_name,
            )

        content = (result.get("response") or "").strip()
        if not content:
            raise AIProviderAPIError(
                "Cloudflare Workers AI returned an empty response.",
                provider=self.provider_name,
            )

        usage = result.get("usage") or body.get("usage") or {}
        neurons = usage.get("neurons")
        metadata = {
            "provider_finish_reason": result.get("finish_reason"),
            "max_output_tokens": payload_max_tokens(result),
        }
        if neurons is not None:
            metadata["neurons"] = neurons
            metadata["estimated_cost_usd"] = round(float(neurons) / 1000 * 0.011, 8)

        logger.info(
            "[CLOUDFLARE-AI] Generated %d tokens in %.2fs (neurons=%s)",
            usage.get("completion_tokens", 0), duration, neurons,
        )

        return LLMResponse(
            content=content,
            provider=self.provider_name,
            model=result.get("model") or self.model_name,
            finish_reason=(result.get("finish_reason") or "stop"),
            prompt_tokens=int(usage.get("prompt_tokens", 0) or 0),
            completion_tokens=int(usage.get("completion_tokens", 0) or 0),
            total_tokens=int(usage.get("total_tokens", 0) or 0),
            metadata=metadata,
        )


def payload_max_tokens(result: dict) -> Optional[int]:
    """The token ceiling the model was given, if it reported one."""
    usage = result.get("usage") or {}
    value = usage.get("max_tokens")
    return int(value) if isinstance(value, (int, float)) else None
