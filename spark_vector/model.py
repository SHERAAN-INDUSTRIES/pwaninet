"""
ONNX Runtime wrapper serving multilingual-e5-base (int8) for spark-vector.

Load-time guarantees (design doc §4):
- assets present (baked at image build; MANIFEST.json recorded),
- graph inputs are ones we can feed safely,
- a warm-up embed proves the output dimension == expected (768) — otherwise
  the service REFUSES TO SERVE rather than poison the corpus.
"""

import json
import logging
from pathlib import Path
from typing import List, Sequence, Tuple

import numpy as np
import onnxruntime as ort
from transformers import AutoTokenizer

from embedding_logic import l2_normalize, mean_pool, prefix_texts

logger = logging.getLogger("spark_vector.model")

MODEL_REL_PATH = "onnx/model_int8.onnx"
_KNOWN_GRAPH_INPUTS = {"input_ids", "attention_mask", "token_type_ids"}


class ModelLoadError(Exception):
    """Missing/corrupt assets, wrong dimension, unsupported graph — refuses to serve."""


class DimensionError(Exception):
    """Runtime dimension guard: a vector did not match the expected size."""


class EmbeddingModel:
    def __init__(
        self,
        assets_dir: str,
        expected_dim: int = 768,
        max_length: int = 512,
        intra_threads: int = None,
    ):
        self.assets_dir = Path(assets_dir)
        self.expected_dim = expected_dim
        self.max_length = max_length
        self.intra_threads = intra_threads
        self.tokenizer = None
        self.session = None
        self.input_names: List[str] = []
        self.manifest: dict = {}

    @property
    def warm(self) -> bool:
        return self.session is not None

    def load(self) -> None:
        onnx_path = self.assets_dir / MODEL_REL_PATH
        if not onnx_path.is_file():
            raise ModelLoadError(
                f"Model asset missing: {onnx_path} — assets are baked at image build "
                "(see spark_vector/fetch_assets.py); the service never downloads at boot."
            )

        manifest_path = self.assets_dir / "MANIFEST.json"
        if manifest_path.is_file():
            self.manifest = json.loads(manifest_path.read_text())

        try:
            self.tokenizer = AutoTokenizer.from_pretrained(
                str(self.assets_dir), local_files_only=True
            )
        except Exception as exc:  # noqa: BLE001 - any failure here means we cannot serve
            raise ModelLoadError(f"Failed to load tokenizer from {self.assets_dir}: {exc}") from exc

        session_options = ort.SessionOptions()
        if self.intra_threads:
            session_options.intra_op_num_threads = int(self.intra_threads)
        try:
            self.session = ort.InferenceSession(
                str(onnx_path),
                sess_options=session_options,
                providers=["CPUExecutionProvider"],
            )
        except Exception as exc:  # noqa: BLE001
            raise ModelLoadError(f"Failed to load ONNX session {onnx_path}: {exc}") from exc

        self.input_names = [item.name for item in self.session.get_inputs()]
        unknown = [name for name in self.input_names if name not in _KNOWN_GRAPH_INPUTS]
        if unknown:
            raise ModelLoadError(
                f"ONNX graph has unexpected inputs {unknown}; cannot feed them safely."
            )

        # Warm the full path AND enforce the 768-dim invariant at the edge.
        vectors, _ = self.embed(["dimension probe"], "document")
        if len(vectors[0]) != self.expected_dim:
            raise ModelLoadError(
                f"Model produces {len(vectors[0])}-dim vectors, expected {self.expected_dim}. "
                "Refusing to serve (dimension invariant, design doc §4)."
            )
        logger.info(
            "model loaded: assets=%s dim=%s inputs=%s manifest_revision=%s",
            self.assets_dir,
            self.expected_dim,
            self.input_names,
            self.manifest.get("revision", "unknown"),
        )

    def embed(self, texts: Sequence[str], kind: str) -> Tuple[List[List[float]], int]:
        """
        Embed texts for 'query' or 'document' (prefixes applied inside).

        Returns (vectors, prompt_tokens).
        """
        if self.session is None or self.tokenizer is None:
            raise ModelLoadError("Model not loaded.")

        prefixed = prefix_texts(texts, kind)
        encoded = self.tokenizer(
            list(prefixed),
            return_tensors="np",
            padding=True,
            truncation=True,
            max_length=self.max_length,
        )
        feed = {
            "input_ids": encoded["input_ids"].astype(np.int64),
            "attention_mask": encoded["attention_mask"].astype(np.int64),
        }
        if "token_type_ids" in self.input_names:
            # XLM-R does not use segment ids; feed explicit zeros when the graph wants them.
            feed["token_type_ids"] = np.zeros_like(feed["input_ids"])

        output_names = [item.name for item in self.session.get_outputs()]
        outputs = self.session.run(None, feed)
        hidden = (
            outputs[output_names.index("last_hidden_state")]
            if "last_hidden_state" in output_names
            else outputs[0]
        )

        pooled = mean_pool(hidden, feed["attention_mask"])
        vectors = l2_normalize(pooled)

        if vectors.shape[-1] != self.expected_dim:
            raise DimensionError(
                f"Model produced {vectors.shape[-1]}-dim vectors, expected {self.expected_dim}."
            )

        prompt_tokens = int(feed["attention_mask"].sum())
        return [vector.tolist() for vector in vectors], prompt_tokens
