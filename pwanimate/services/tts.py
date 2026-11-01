"""Server-side text-to-speech providers for Spark replies."""

import html
import logging
import re
from dataclasses import dataclass

import requests
from django.conf import settings

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SpeechVoice:
    """Public voice metadata mapped to a provider-specific voice name."""

    provider_name: str
    locale: str


VOICES = {
    "warm_female": SpeechVoice("en-US-JennyNeural", "en-US"),
    "warm_male": SpeechVoice("en-US-GuyNeural", "en-US"),
    "academic_female": SpeechVoice("en-US-AriaNeural", "en-US"),
    "academic_male": SpeechVoice("en-US-DavisNeural", "en-US"),
}


class TTSConfigurationError(RuntimeError):
    """Raised when the configured TTS provider cannot be used."""


class TTSProviderError(RuntimeError):
    """Raised when a provider rejects or cannot complete synthesis."""


def text_for_speech(value: str) -> str:
    """Reduce markdown to bounded, readable plain text."""
    text = str(value or "")
    text = re.sub(r"```[\s\S]*?```", " ", text)
    text = re.sub(r"\$\$[\s\S]*?\$\$", " ", text)
    text = re.sub(r"`([^`]+)`", r"\1", text)
    text = re.sub(r"!\[([^\]]*)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"^\s{0,3}#{1,6}\s+", "", text, flags=re.MULTILINE)
    text = re.sub(r"^\s{0,3}>\s?", "", text, flags=re.MULTILINE)
    text = re.sub(r"^\s*[-*+]\s+", "", text, flags=re.MULTILINE)
    text = re.sub(r"^\s*\d+\.\s+", "", text, flags=re.MULTILINE)
    text = re.sub(r"(\*\*|__|~~|\*|_)", "", text)
    return re.sub(r"\s+", " ", text).strip()


def synthesize(text: str, voice_id: str = "warm_female") -> bytes:
    """Synthesize speech through the configured Azure Speech resource."""
    if getattr(settings, "PWANIMATE_TTS_PROVIDER", "azure").lower() != "azure":
        raise TTSConfigurationError("The configured TTS provider is not supported.")

    key = str(getattr(settings, "PWANIMATE_AZURE_SPEECH_KEY", "") or "").strip()
    region = str(getattr(settings, "PWANIMATE_AZURE_SPEECH_REGION", "") or "").strip()
    if not key or not region:
        raise TTSConfigurationError("Azure Speech credentials are not configured.")

    voice = VOICES.get(voice_id)
    if voice is None:
        raise TTSConfigurationError("Unknown Spark voice.")

    speech_text = text_for_speech(text)
    max_chars = int(getattr(settings, "PWANIMATE_TTS_MAX_CHARACTERS", 4000))
    if not speech_text:
        raise TTSProviderError("There is no readable text to synthesize.")
    if len(speech_text) > max_chars:
        speech_text = speech_text[:max_chars].rsplit(" ", 1)[0].strip()

    ssml = (
        f'<speak version="1.0" xml:lang="{voice.locale}" '
        f'xmlns="http://www.w3.org/2001/10/synthesis">'
        f'<voice name="{voice.provider_name}">{html.escape(speech_text)}</voice>'
        "</speak>"
    )
    endpoint = f"https://{region}.tts.speech.microsoft.com/cognitiveservices/v1"
    try:
        response = requests.post(
            endpoint,
            headers={
                "Ocp-Apim-Subscription-Key": key,
                "Content-Type": "application/ssml+xml",
                "X-Microsoft-OutputFormat": "audio-24khz-48kbitrate-mono-mp3",
                "User-Agent": "Vasto-Spark-TTS",
            },
            data=ssml.encode("utf-8"),
            timeout=float(getattr(settings, "PWANIMATE_TTS_TIMEOUT", 15)),
        )
    except requests.RequestException as exc:
        raise TTSProviderError("Azure Speech could not be reached.") from exc

    if response.status_code >= 400:
        logger.warning("Azure Speech returned HTTP %s.", response.status_code)
        raise TTSProviderError("Azure Speech rejected the synthesis request.")
    if not response.content:
        raise TTSProviderError("Azure Speech returned an empty audio response.")
    return response.content
