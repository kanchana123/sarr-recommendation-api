"""CPU ONNX query embedder — no PyTorch on the request path.

Lambda + API Gateway cannot absorb a 30s+ ``import torch``. The Lambda image
exports this ONNX graph at build time from the same bi-encoder used in ETL.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import numpy as np

logger = logging.getLogger("sarr.api")


def onnx_model_dir(model_name: str) -> Path | None:
    path = Path(model_name)
    if path.is_dir() and (path / "model.onnx").is_file():
        return path
    return None


def pool_and_normalize(
    hidden: np.ndarray,
    attention_mask: np.ndarray,
    pooling: str,
) -> np.ndarray:
    """Pool transformer tokens to one vector and L2-normalize (BGE-style)."""
    if pooling == "cls":
        pooled = hidden[:, 0, :]
    elif pooling == "mean":
        mask = attention_mask.astype(np.float32)[:, :, None]
        pooled = (hidden * mask).sum(axis=1) / np.clip(mask.sum(axis=1), 1e-9, None)
    else:
        raise ValueError(f"unsupported pooling: {pooling}")
    norms = np.linalg.norm(pooled, axis=1, keepdims=True)
    return pooled / np.clip(norms, 1e-12, None)


class OnnxQueryEmbedder:
    """Loads tokenizer.json + model.onnx from a local directory."""

    def __init__(self, model_dir: str | Path) -> None:
        self.model_dir = Path(model_dir)
        config_path = self.model_dir / "embed_config.json"
        config = json.loads(config_path.read_text()) if config_path.is_file() else {}
        self.pooling = str(config.get("pooling", "cls"))
        self.max_length = int(config.get("max_length", 512))

        logger.info("importing onnxruntime")
        import onnxruntime as ort
        from tokenizers import Tokenizer

        tokenizer_path = self.model_dir / "tokenizer.json"
        logger.info("loading onnx embedder from %s", self.model_dir)
        self._tokenizer = Tokenizer.from_file(str(tokenizer_path))
        self._tokenizer.enable_truncation(max_length=self.max_length)
        self._session = ort.InferenceSession(
            str(self.model_dir / "model.onnx"),
            providers=["CPUExecutionProvider"],
        )
        self._input_names = {inp.name for inp in self._session.get_inputs()}

    def embed(self, text: str) -> list[float]:
        encoded = self._tokenizer.encode(text)
        input_ids = np.asarray([encoded.ids], dtype=np.int64)
        attention_mask = np.asarray([encoded.attention_mask], dtype=np.int64)
        token_type_ids = np.asarray([encoded.type_ids], dtype=np.int64)

        feeds: dict[str, np.ndarray] = {}
        if "input_ids" in self._input_names:
            feeds["input_ids"] = input_ids
        if "attention_mask" in self._input_names:
            feeds["attention_mask"] = attention_mask
        if "token_type_ids" in self._input_names:
            feeds["token_type_ids"] = token_type_ids

        hidden = self._session.run(None, feeds)[0]
        vector = pool_and_normalize(hidden, attention_mask, self.pooling)
        return np.asarray(vector[0], dtype=np.float32).tolist()
