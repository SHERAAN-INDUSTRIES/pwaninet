# spark-vector — self-hosted embedding worker (design doc §4)
#
# Weights are BAKED AT BUILD TIME at a pinned revision + checksum; the
# container never downloads at boot. No torch — the int8 ONNX export of
# intfloat/multilingual-e5-base is fetched from its pinned transformers.js
# mirror (portable dynamic int8, no CPU-specific VNNI instructions).

FROM python:3.12-slim

ARG MODEL_REPO=Xenova/multilingual-e5-base
# Pinned commit (immutable) — bump deliberately, never float to main:
ARG MODEL_REVISION=1ec9243030a27d1a115d5c340572074c125b58b2
# sha256 of onnx/model_int8.onnx at that revision (verified at build):
ARG MODEL_SHA256=9ddfd8b45086dabc59a7e1bb00463225dace8954962418b240840f2153bc87da

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    HF_HUB_OFFLINE=1 \
    TRANSFORMERS_OFFLINE=1 \
    SPARK_VECTOR_ASSETS_DIR=/app/assets \
    SPARK_VECTOR_MODEL_NAME=multilingual-e5-base \
    SPARK_VECTOR_EXPECTED_DIMENSIONS=768

WORKDIR /app

# Deps first (cached unless requirements change).
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Bake model + tokenizer at the pinned revision, verify checksum, write MANIFEST.
COPY fetch_assets.py .
RUN python fetch_assets.py \
      --repo "${MODEL_REPO}" \
      --revision "${MODEL_REVISION}" \
      --sha256 "${MODEL_SHA256}" \
      --out /app/assets

# Application code last (cheapest to iterate).
COPY embedding_logic.py model.py app.py bench.py ./

EXPOSE 8100

HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8100/healthz', timeout=4).status == 200 else 1)"

# Single worker: one model copy in RAM (design doc §4).
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8100", "--workers", "1"]
