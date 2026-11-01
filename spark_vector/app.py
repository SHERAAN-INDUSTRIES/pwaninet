"""
spark-vector — self-hosted embedding microservice (design doc §4).

OpenAI-compatible POST /v1/embeddings with a task_type extension, plus
GET /v1/models and GET /healthz. Single uvicorn worker, one model copy in
RAM, inference serialized through a gate (queue_depth reflects waiters).

Not published to host ports — internal pwaninet_net traffic only.
"""

import asyncio
import logging
import os
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from typing import Any, Dict, List, Optional, Union

from fastapi import FastAPI, Header
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from embedding_logic import DEFAULT_TASK_TYPE, normalize_task_type
from model import DimensionError, EmbeddingModel, ModelLoadError

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
logger = logging.getLogger("spark_vector")

MODEL_NAME = os.environ.get("SPARK_VECTOR_MODEL_NAME", "multilingual-e5-base")
EXPECTED_DIMENSIONS = int(os.environ.get("SPARK_VECTOR_EXPECTED_DIMENSIONS", "768"))
MAX_INPUTS = int(os.environ.get("SPARK_VECTOR_MAX_INPUTS", "64"))
MAX_LENGTH = int(os.environ.get("SPARK_VECTOR_MAX_LENGTH", "512"))
ASSETS_DIR = os.environ.get("SPARK_VECTOR_ASSETS_DIR", "/app/assets")
# One request may not exceed this token budget: a 64x512 batch would hold the
# single inference slot for minutes and starve interactive chat queries.
MAX_TOKENS_PER_REQUEST = int(os.environ.get("SPARK_VECTOR_MAX_TOKENS_PER_REQUEST", "4096"))
# Backlog cap: past this we answer 503 so callers back off instead of piling
# onto a busy box (Celery retries with backoff, the query path never queues).
MAX_WAITERS = int(os.environ.get("SPARK_VECTOR_MAX_WAITERS", "32"))
INFER_CONCURRENCY = max(1, int(os.environ.get("SPARK_VECTOR_INFER_CONCURRENCY", "1")))
INTRA_THREADS = os.environ.get("SPARK_VECTOR_INTRA_THREADS", "")
TOKEN = os.environ.get("SPARK_VECTOR_TOKEN", "")

state: Dict[str, Any] = {"model": None, "warm": False, "waiting": 0}


class _BacklogFull(Exception):
    """Too many requests are already waiting for the inference slot."""


class _AdmissionTimeout(Exception):
    """The request waited too long for an inference slot."""


class _Admission:
    """
    Bounded, priority-aware admission control for the inference slot.

    Async-native on purpose: a blocking wait here would stall the event loop,
    which would stall every other request (including the one holding the
    slot) — the service would appear to hang under any overlap.

    - Queries (one per chat message) are admitted ahead of bulk document
      batches, so ingestion can never starve the interactive path.
    - Bulk proceeds whenever no query is waiting, so ingestion can never be
      starved forever either.
    - Backlogs above MAX_WAITERS are rejected (503) instead of queued, so
      callers retry with backoff rather than accumulating work we cannot finish.
    """

    def __init__(self, concurrency: int, max_waiters: int):
        self.concurrency = concurrency
        self.max_waiters = max_waiters
        self._active = 0
        self._waiters = {"query": 0, "document": 0}
        # Woken whenever a slot frees; waiters re-check the admission rule.
        self._wakeup = asyncio.Event()

    def snapshot(self) -> Dict[str, int]:
        return {
            "active": self._active,
            "waiting": self._waiters["query"] + self._waiters["document"],
            "waiting_query": self._waiters["query"],
            "waiting_document": self._waiters["document"],
        }

    def _may_start(self, lane: str) -> bool:
        if self._active >= self.concurrency:
            return False
        if lane == "query":
            return True
        # Bulk only starts when no interactive query is waiting.
        return self._waiters["query"] == 0

    async def acquire(self, lane: str, timeout: float = 120.0) -> None:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        if self._waiters[lane] >= self.max_waiters:
            raise _BacklogFull(lane)
        self._waiters[lane] += 1
        try:
            while True:
                if self._may_start(lane):
                    self._active += 1
                    return
                remaining = deadline - loop.time()
                if remaining <= 0:
                    raise _AdmissionTimeout(lane)
                # Clear-then-recheck: never lose a wakeup from a release that
                # lands between the check and the wait.
                self._wakeup.clear()
                if self._may_start(lane):
                    self._active += 1
                    return
                try:
                    await asyncio.wait_for(
                        self._wakeup.wait(), timeout=min(remaining, 0.5)
                    )
                except asyncio.TimeoutError:
                    pass
        finally:
            self._waiters[lane] -= 1

    def release(self) -> None:
        self._active = max(0, self._active - 1)
        self._wakeup.set()


_admission = _Admission(concurrency=INFER_CONCURRENCY, max_waiters=MAX_WAITERS)

# Dedicated inference pool: a cancelled request can never starve the shared
# event-loop threadpool (the service is single-slot by design).
_executor = ThreadPoolExecutor(
    max_workers=INFER_CONCURRENCY,
    thread_name_prefix="spark-vector-infer",
)


def _estimate_tokens(text: str) -> int:
    """Rough token count for the per-request budget (chars/3, capped)."""
    return min(MAX_LENGTH, max(1, int(len(text) / 3.0) + 1))


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info(
        "spark-vector starting: model=%s dim=%s max_inputs=%s assets=%s concurrency=%s",
        MODEL_NAME, EXPECTED_DIMENSIONS, MAX_INPUTS, ASSETS_DIR, INFER_CONCURRENCY,
    )
    model = EmbeddingModel(
        ASSETS_DIR,
        expected_dim=EXPECTED_DIMENSIONS,
        max_length=MAX_LENGTH,
        intra_threads=int(INTRA_THREADS) if INTRA_THREADS else None,
    )
    try:
        model.load()
    except ModelLoadError as exc:
        # Spec §4: refuse to serve a model whose dimension/assets are wrong.
        logger.error("spark-vector REFUSING TO SERVE: %s", exc)
        raise
    state["model"] = model
    state["warm"] = True
    logger.info("spark-vector warm; manifest revision=%s", model.manifest.get("revision", "unknown"))
    yield
    state["warm"] = False
    state["model"] = None


app = FastAPI(title="spark-vector", version="1.0.0", lifespan=lifespan)


class EmbeddingsPayload(BaseModel):
    model: Optional[str] = None
    input: Union[str, List[str]]
    task_type: str = Field(default=DEFAULT_TASK_TYPE)
    user: Optional[str] = None  # OpenAI passthrough, ignored


def _error(status: int, message: str, err_type: str, headers: Optional[Dict[str, str]] = None) -> JSONResponse:
    return JSONResponse(
        status_code=status,
        content={"error": {"message": message, "type": err_type}},
        headers=headers,
    )


def _auth_error(authorization: Optional[str]) -> Optional[JSONResponse]:
    """Optional shared-bearer protection (§5: PWANIMATE_SELFHOSTED_EMBEDDINGS_TOKEN)."""
    if not TOKEN:
        return None
    if authorization != f"Bearer {TOKEN}":
        return _error(401, "Missing or invalid bearer token.", "authentication_error")
    return None


@app.get("/healthz")
def healthz() -> Dict[str, Any]:
    snapshot = _admission.snapshot()
    return {
        "status": "ok" if state["warm"] else "loading",
        "model": MODEL_NAME,
        "warm": state["warm"],
        "queue_depth": snapshot["waiting"],
        "active": snapshot["active"],
        "waiting_query": snapshot["waiting_query"],
        "waiting_document": snapshot["waiting_document"],
    }


@app.get("/v1/models")
def list_models(authorization: Optional[str] = Header(default=None)):
    auth_error = _auth_error(authorization)
    if auth_error is not None:
        return auth_error
    return {
        "object": "list",
        "data": [{"object": "model", "id": MODEL_NAME, "owned_by": "spark"}],
    }


@app.post("/v1/embeddings")
async def embeddings(
    payload: EmbeddingsPayload,
    authorization: Optional[str] = Header(default=None),
):
    auth_error = _auth_error(authorization)
    if auth_error is not None:
        return auth_error

    model: Optional[EmbeddingModel] = state["model"]
    if model is None or not state["warm"]:
        return _error(503, "Model is loading.", "service_unavailable")

    # D5 protection: the served model id must match what clients claim (the
    # Django stamp equals this string — a mismatch would poison re-embed logic).
    if payload.model is not None and payload.model != MODEL_NAME:
        return _error(
            400,
            f"Unknown model '{payload.model}'. This worker serves '{MODEL_NAME}'.",
            "invalid_request_error",
        )

    texts = [payload.input] if isinstance(payload.input, str) else payload.input
    if not texts:
        return _error(400, "'input' must not be empty.", "invalid_request_error")
    if len(texts) > MAX_INPUTS:
        return _error(
            400,
            f"Too many inputs: {len(texts)} > {MAX_INPUTS} (spark-vector input cap).",
            "invalid_request_error",
        )
    if any(not isinstance(text, str) for text in texts):
        return _error(400, "'input' items must all be strings.", "invalid_request_error")

    try:
        kind = normalize_task_type(payload.task_type)
    except ValueError as exc:
        return _error(400, str(exc), "invalid_request_error")

    # Token budget: fail fast rather than monopolise the single inference slot
    # (a 64x512 batch would hold it for minutes and starve chat queries).
    estimated_tokens = sum(_estimate_tokens(text) for text in texts)
    if estimated_tokens > MAX_TOKENS_PER_REQUEST:
        return _error(
            413,
            (
                f"Request too large: ~{estimated_tokens} tokens exceeds the "
                f"{MAX_TOKENS_PER_REQUEST}-token budget per request. "
                "Split the batch into smaller requests."
            ),
            "invalid_request_error",
        )

    # Admission: interactive queries first, bounded backlog, one slot.
    lane = "query" if kind == "query" else "document"
    wait_started = time.perf_counter()
    try:
        await _admission.acquire(lane)
    except _BacklogFull:
        return _error(
            503,
            "spark-vector is busy — too many requests are queued. Retry shortly.",
            "service_unavailable",
            headers={"Retry-After": "5"},
        )
    except _AdmissionTimeout:
        return _error(
            503,
            "spark-vector timed out waiting for an inference slot. Retry shortly.",
            "service_unavailable",
            headers={"Retry-After": "5"},
        )
    waited_ms = (time.perf_counter() - wait_started) * 1000.0

    try:
        loop = asyncio.get_running_loop()
        embed_started = time.perf_counter()
        vectors, prompt_tokens = await loop.run_in_executor(
            _executor, model.embed, texts, kind
        )
        embed_ms = (time.perf_counter() - embed_started) * 1000.0
        logger.info(
            "embed lane=%s texts=%d waited=%.1fms inference=%.1fms",
            lane, len(texts), waited_ms, embed_ms,
        )
    except ModelLoadError as exc:
        logger.error("embed failed: %s", exc)
        return _error(503, "Model unavailable.", "service_unavailable")
    except DimensionError as exc:
        # Edge guard: never emit a vector that would break the 768 invariant.
        logger.error("dimension guard tripped: %s", exc)
        return _error(500, str(exc), "internal_error")
    finally:
        _admission.release()

    return {
        "object": "list",
        "model": MODEL_NAME,
        "data": [
            {"object": "embedding", "index": index, "embedding": vector}
            for index, vector in enumerate(vectors)
        ],
        "usage": {"prompt_tokens": prompt_tokens, "total_tokens": prompt_tokens},
    }
