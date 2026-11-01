"""
Pure embedding math & e5 prefixing for spark-vector (design doc §4).

Deliberately free of fastapi/onnxruntime imports so unit tests run anywhere
numpy exists (host venv or container).

Model contract:
- e5 instruction prefixes: "query: " for queries, "passage: " for documents,
  applied at the service boundary per task_type (idempotent — literal
  "query: "/"passage: " inputs pass through untouched).
- Mean pooling over the attention mask, then L2 normalization.
- Dimension is validated by the caller at the edge (model.py + app.py).
"""

from typing import List, Sequence

import numpy as np

QUERY_PREFIX = "query: "
PASSAGE_PREFIX = "passage: "
DEFAULT_TASK_TYPE = "RETRIEVAL_DOCUMENT"

# task_type aliases accepted at the API boundary (case-insensitive).
_QUERY_TYPES = {"retrieval_query", "query"}
_DOCUMENT_TYPES = {"retrieval_document", "document", "passage"}


def normalize_task_type(task_type: str = None) -> str:
    """
    Map a client task_type onto 'query' or 'document'.

    Raises ValueError for unknown values (API layer turns that into a 400).
    """
    value = (task_type or DEFAULT_TASK_TYPE).strip().lower()
    if value in _QUERY_TYPES:
        return "query"
    if value in _DOCUMENT_TYPES:
        return "document"
    raise ValueError(
        f"Unsupported task_type '{task_type}'. Use RETRIEVAL_QUERY or RETRIEVAL_DOCUMENT."
    )


def apply_prefix(text: str, kind: str) -> str:
    """Apply the e5 instruction prefix; idempotent for already-prefixed input."""
    if text.startswith(QUERY_PREFIX) or text.startswith(PASSAGE_PREFIX):
        return text
    return (QUERY_PREFIX if kind == "query" else PASSAGE_PREFIX) + text


def prefix_texts(texts: Sequence[str], kind: str) -> List[str]:
    return [apply_prefix(str(text), kind) for text in texts]


def mean_pool(hidden_state: np.ndarray, attention_mask: np.ndarray) -> np.ndarray:
    """Masked mean pooling over the token axis: (B, T, H), (B, T) -> (B, H)."""
    mask = attention_mask.astype(np.float32)[:, :, None]
    summed = (hidden_state.astype(np.float32) * mask).sum(axis=1)
    counts = np.maximum(mask.sum(axis=1), 1e-9)
    return (summed / counts).astype(np.float32)


def l2_normalize(vectors: np.ndarray) -> np.ndarray:
    """Row-wise unit L2 norm (e5 recommendation; cosine-safe in pgvector)."""
    norms = np.linalg.norm(vectors, axis=1, keepdims=True)
    return (vectors / np.maximum(norms, 1e-12)).astype(np.float32)
