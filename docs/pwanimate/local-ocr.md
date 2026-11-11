# Pwanimate local image OCR

Uploaded images and scanned PDF pages can be extracted by DeepSeek OCR through the server-side Ollama API. Each result is saved on the attachment and reused as context for later questions. The existing tutor model still generates answers; DeepSeek OCR is not used as a conversational model.

Local development defaults to:

```dotenv
PWANIMATE_ATTACHMENT_VISION_PROVIDER=ollama
PWANIMATE_OLLAMA_BASE_URL=http://127.0.0.1:11434
PWANIMATE_OLLAMA_OCR_MODEL=deepseek-ocr:pwanimate-q4_K_M
PWANIMATE_OLLAMA_OCR_TIMEOUT=180
PWANIMATE_ATTACHMENT_VISION_MAX_TOKENS=4096
TIMETABLE_OCR_PROVIDER=ollama
TIMETABLE_OCR_MODEL=deepseek-ocr:pwanimate-q4_K_M
```

`PWANIMATE_ATTACHMENT_VISION_MODEL` normally follows the Ollama OCR model in local settings. If it is explicitly set, it must match `PWANIMATE_OLLAMA_OCR_MODEL`. Restart Django and the attachment Celery worker after changing these values. In a container, configure the base URL to reach the machine running Ollama; container localhost is a different machine from host localhost.

Image attachment, scanned-PDF, and timetable extraction default to Ollama. Point it at an Ollama server reachable from both Django and Celery; a workstation model is not accessible from the Android phone or production server by its localhost address. The phone uploads images normally; OCR runs on the application server's configured Ollama host. Set `TIMETABLE_OCR_PROVIDER=gemini` only when explicitly choosing Gemini for timetable extraction.

The provider sends one image at a time as base64 bytes using `/api/generate` and the documented `\nFree OCR.` prompt. It reads private uploads through Django storage and also accepts rendered PDF page bytes. It never sends signed media URLs, chat history or tutor instructions to the OCR model. The local extraction route contains only Ollama: connection failure does not silently send the upload to a cloud fallback. Empty, incomplete or token-truncated output is not accepted as a successful extraction. Existing usage limits and one-time-analysis persistence remain in place.

The official `deepseek-ocr:latest` download is BF16 and approximately 6.7 GB, while this workstation's verified `deepseek-ocr:pwanimate-q4_K_M` build is approximately 2.8 GB. The quantized tag is the default because the full model was killed by the OS for running out of memory while encoding an image. The quantized build preserves the vision/projector tensors while reducing text-weight memory.

Protocol references: [Ollama vision API](https://docs.ollama.com/capabilities/vision), [DeepSeek OCR prompts](https://ollama.com/library/deepseek-ocr), [GGUF importing](https://docs.ollama.com/import).

Checks:

```bash
python manage.py test pwanimate.tests.test_ollama_ocr pwanimate.tests.test_capability_routing --settings=pwaninet.settings.test
```

## This workstation

The ignored local `.env` selects `deepseek-ocr:pwanimate-q4_K_M`, a 2,760,988,384-byte copy built from the already downloaded model. Its text weights are Q4_K_M (with the quantizer’s required per-tensor fallbacks); all 476 SAM, vision and projector tensors retain their original data. `deepseek-ocr:latest` remains installed unchanged.

The bundled quantizer hides the image encoder when writing its text-only output. `scripts/restore_deepseek_ocr_vision.py ORIGINAL.gguf QUANTIZED_TEXT.gguf COMPLETE.gguf` restores those unchanged tensors and checks that names and shapes match the original. Import the complete GGUF into Ollama with the original `TEMPLATE {{ .Prompt }}` and `temperature 0`; the verified local tag also sets `num_batch 32` and `num_ctx 4096`. The intermediate GGUF files are not application assets and need not be deployed.

A real call through Pwanimate’s AI gateway returned `PWANIMATE OCR TEST` and `Total: 42` from a generated test image in 102.51 seconds on the CPU. This proves image extraction, not production throughput or handwriting accuracy. Follow-up questions reuse the stored extraction. The verification record is in `docs/verification/pwanimate-local-ocr.json`.
