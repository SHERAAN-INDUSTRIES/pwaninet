"""Local DeepSeek OCR for one-time image and scanned-page extraction."""
import base64
from urllib.parse import urlsplit

import requests
from django.conf import settings

from pwanimate.ai.exceptions import (
    AIProviderAPIError, AIProviderConfigurationError, AIProviderTimeoutError,
)
from pwanimate.ai.gateway.base import BaseLLMProvider
from pwanimate.ai.gateway.types import LLMResponse
from pwanimate.ai.vision_images import prepare_image_for_vision


class OllamaOCRProvider(BaseLLMProvider):
    provider_name = "ollama"
    tasks = {"attachment_vision_analysis", "attachment_vision_extraction", "timetable_vision_extraction"}

    def __init__(self):
        self.base_url = str(getattr(settings, "PWANIMATE_OLLAMA_BASE_URL", "http://127.0.0.1:11434")).rstrip("/")
        self.model_name = getattr(settings, "PWANIMATE_OLLAMA_OCR_MODEL", "deepseek-ocr:pwanimate-q4_K_M")
        # Timetable extraction may pin a different local OCR model.
        self.allowed_models = {
            self.model_name,
            str(getattr(settings, "TIMETABLE_OCR_MODEL", "") or ""),
        } - {""}
        self.timeout = float(getattr(settings, "PWANIMATE_OLLAMA_OCR_TIMEOUT", 180))

    def is_available(self):
        url = urlsplit(self.base_url)
        return bool(url.scheme in {"http", "https"} and url.hostname and not url.username and not url.password and not url.query and not url.fragment and self.model_name)

    def _image_bytes(self, request):
        images = {}
        for message in request.messages:
            for attachment in message.attachments:
                if getattr(attachment, "attachment_type", "") == "image":
                    images[getattr(attachment, "id", None) or id(attachment)] = attachment
        for attachment in request.attachments:
            if getattr(attachment, "attachment_type", "") == "image":
                images[getattr(attachment, "id", None) or id(attachment)] = attachment
        if len(images) != 1:
            raise AIProviderConfigurationError("DeepSeek OCR requires exactly one image per extraction.", provider=self.provider_name)
        attachment = next(iter(images.values()))
        limit = int(getattr(settings, "PWANIMATE_OLLAMA_OCR_MAX_IMAGE_BYTES", 20 * 1024 * 1024))
        data = getattr(attachment, "data_bytes", None)
        try:
            if not data and getattr(attachment, "file_path", None):
                with open(attachment.file_path, "rb") as handle:
                    data = handle.read(limit + 1)
            if not data and getattr(attachment, "file", None):
                # Django storage may be local or private S3; never send a signed
                # download URL to the model or depend on a public media endpoint.
                with attachment.file.open("rb") as handle:
                    data = handle.read(limit + 1)
        except (OSError, ValueError) as exc:
            raise AIProviderAPIError("Could not read the uploaded image for OCR.", provider=self.provider_name) from exc
        if not data or len(data) > limit:
            raise AIProviderAPIError("OCR image is empty, unavailable, or exceeds the image size limit.", provider=self.provider_name)
        data, _mime = prepare_image_for_vision(data, getattr(attachment, "mime_type", "image/png"))
        return base64.b64encode(data).decode("ascii")

    def generate(self, request):
        if not self.is_available():
            raise AIProviderConfigurationError("Configure PWANIMATE_OLLAMA_BASE_URL and PWANIMATE_OLLAMA_OCR_MODEL.", provider=self.provider_name)
        if request.task not in self.tasks:
            raise AIProviderConfigurationError("DeepSeek OCR is an extraction model; use the chat model for answers.", provider=self.provider_name)
        model = request.model or self.model_name
        if model not in self.allowed_models:
            raise AIProviderConfigurationError("The requested Ollama OCR model is not configured.", provider=self.provider_name)
        # DeepSeek OCR is sensitive to punctuation/newlines. Use its documented
        # OCR prompt, not the tutor's system instructions or conversation history.
        # Structured tasks (e.g. timetable extraction) carry their own prompt and
        # need Ollama's JSON mode so the output parses cleanly.
        structured = request.task == "timetable_vision_extraction"
        prompt = ""
        if structured:
            prompt = "\n".join(
                msg.content for msg in request.messages if getattr(msg, "role", "") == "user"
            ).strip() or "Extract the timetable."
        else:
            prompt = "\nFree OCR."
        payload = {
            "model": model,
            "prompt": prompt,
            "images": [self._image_bytes(request)],
            "stream": False,
            "options": {"temperature": 0, "num_predict": request.max_tokens},
        }
        if structured:
            payload["format"] = "json"
        try:
            response = requests.post(
                self.base_url + "/api/generate", json=payload,
                timeout=(float(getattr(settings, "PWANIMATE_LLM_CONNECT_TIMEOUT", 2)), self.timeout),
                allow_redirects=False,
            )
        except requests.exceptions.Timeout as exc:
            raise AIProviderTimeoutError("Local Ollama OCR timed out.", provider=self.provider_name) from exc
        except requests.exceptions.RequestException as exc:
            raise AIProviderAPIError("Could not connect to local Ollama OCR.", provider=self.provider_name) from exc
        if response.status_code != 200:
            raise AIProviderAPIError("Ollama OCR request failed. Confirm the configured model is installed and the server is running.", provider=self.provider_name, status_code=response.status_code)
        try:
            result = response.json()
            content = result.get("response", "")
            if not isinstance(content, str) or not content.strip() or result.get("error") or result.get("done") is not True:
                raise ValueError("Incomplete OCR response")
            if result.get("done_reason") == "length":
                raise ValueError("OCR output reached its token limit")
        except (ValueError, TypeError, AttributeError) as exc:
            raise AIProviderAPIError("Ollama returned empty or incomplete OCR output.", provider=self.provider_name) from exc
        try:
            prompt_tokens = int(result.get("prompt_eval_count", 0) or 0)
            completion_tokens = int(result.get("eval_count", 0) or 0)
        except (ValueError, TypeError) as exc:
            raise AIProviderAPIError("Ollama returned invalid OCR usage metadata.", provider=self.provider_name) from exc
        return LLMResponse(
            content=content.strip(), provider=self.provider_name, model=model,
            finish_reason=result.get("done_reason", "stop"), prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens, total_tokens=prompt_tokens + completion_tokens,
            metadata={"extraction": "ocr", "local": True},
        )
