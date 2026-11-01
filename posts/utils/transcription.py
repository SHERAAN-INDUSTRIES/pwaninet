"""
Audio extraction and speech-to-text transcription utilities for video posts.

Uses FFmpeg to extract 16kHz mono PCM audio and faster-whisper for local speech recognition.
"""

import os
import re
import shutil
import logging
import subprocess
from typing import Optional

logger = logging.getLogger(__name__)

MAX_TRANSCRIPT_CHARS = 50_000


def clean_transcript_text(text: str, max_chars: int = MAX_TRANSCRIPT_CHARS) -> str:
    """Clean and sanitize transcript text: strip null bytes, normalize whitespace, cap length."""
    if not text:
        return ""
    # Strip null bytes to prevent PostgreSQL text field insertion errors
    text = text.replace('\x00', '')
    # Normalize horizontal whitespace
    text = re.sub(r'[ \t]+', ' ', text)
    # Normalize newlines
    text = re.sub(r'\n{3,}', '\n\n', text)
    text = text.strip()
    if len(text) > max_chars:
        text = text[:max_chars]
    return text


def extract_audio_from_video(video_path: str, output_audio_path: str) -> bool:
    """
    Extract 16kHz mono audio (pcm_s16le) from a video file using FFmpeg.

    Args:
        video_path: Path to source video file.
        output_audio_path: Target path for extracted .wav file.

    Returns:
        bool: True if extraction succeeded with exit code 0, False otherwise.
    """
    if not video_path or not os.path.exists(video_path):
        logger.error("Audio extraction failed: Video file does not exist at %s", video_path)
        return False

    ffmpeg_bin = shutil.which('ffmpeg') or '/usr/bin/ffmpeg'

    out_dir = os.path.dirname(output_audio_path)
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)

    cmd = [
        ffmpeg_bin,
        '-y',
        '-i', video_path,
        '-vn',
        '-ar', '16000',
        '-ac', '1',
        '-c:a', 'pcm_s16le',
        output_audio_path,
    ]

    try:
        logger.info("Extracting audio with FFmpeg: %s -> %s", video_path, output_audio_path)
        result = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            logger.warning(
                "FFmpeg audio extraction failed (exit %d): %s",
                result.returncode,
                result.stderr[-500:] if result.stderr else "Unknown error"
            )
            return False

        if os.path.exists(output_audio_path) and os.path.getsize(output_audio_path) > 0:
            logger.info("Audio extracted successfully (%d bytes)", os.path.getsize(output_audio_path))
            return True

        logger.warning("FFmpeg succeeded but output audio file is missing or empty: %s", output_audio_path)
        return False

    except Exception as exc:
        logger.warning("Unexpected error extracting audio from video %s: %s", video_path, exc, exc_info=True)
        return False


def transcribe_audio_file(audio_path: str, model_size: str = "tiny") -> str:
    """
    Transcribe speech from an audio file using faster-whisper.

    Gracefully falls back and returns empty string if faster-whisper is not installed.

    Args:
        audio_path: Path to audio file (e.g. 16kHz mono .wav).
        model_size: Model size name (default: "tiny").

    Returns:
        str: Sanitized transcription text capped at MAX_TRANSCRIPT_CHARS.
    """
    if not audio_path or not os.path.exists(audio_path):
        logger.warning("Transcription skipped: Audio file does not exist at %s", audio_path)
        return ""

    try:
        from faster_whisper import WhisperModel
    except ImportError:
        logger.warning("faster-whisper not installed; skipping audio transcription")
        return ""

    from pwaninet.compat import patch_av_metadata_errors

    patch_av_metadata_errors()

    try:
        cpu_threads = min(2, max(1, (os.cpu_count() or 1) // 2))
        logger.info("Initializing faster-whisper model '%s' on CPU (int8, %d threads)...", model_size, cpu_threads)
        model = WhisperModel(model_size, device="cpu", compute_type="int8", cpu_threads=cpu_threads)
        
        segments, info = model.transcribe(audio_path, beam_size=1)
        
        extracted_text_list = []
        for segment in segments:
            if segment.text and segment.text.strip():
                extracted_text_list.append(segment.text.strip())

        raw_transcript = " ".join(extracted_text_list)
        clean_text = clean_transcript_text(raw_transcript)
        logger.info(
            "Transcription completed for %s: %d characters (language: %s, prob: %.2f)",
            audio_path,
            len(clean_text),
            getattr(info, 'language', 'unknown'),
            getattr(info, 'language_probability', 0.0)
        )
        return clean_text

    except Exception as exc:
        logger.warning("Error during transcription of %s: %s", audio_path, exc, exc_info=True)
        return ""
