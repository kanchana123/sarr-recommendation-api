"""Query embedding for online search (Lambda / local API)."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

import numpy as np

from sarr.api.model_source import resolve_model_source
from sarr.api.onnx_embedder import onnx_model_dir
from sarr.common.config import get_settings

if TYPE_CHECKING:
    from sentence_transformers import SentenceTransformer

logger = logging.getLogger("sarr.api")


class QueryEmbedder:
    """Lazy-loads the bi-encoder once per process (warm Lambda reuse).

    Lambda serves ONNX via onnxruntime (PyTorch import exceeds API Gateway's
    30s limit). Local/dev still uses sentence-transformers when no ONNX graph
    is present.
    """

    def __init__(self, model_name: str | None = None) -> None:
        self.model_name = model_name or get_settings().embedding_model
        self._model: SentenceTransformer | None = None
        self._onnx: Any | None = None

    def embed(self, text: str) -> list[float]:
        onnx_dir = onnx_model_dir(self.model_name)
        if onnx_dir is not None:
            if self._onnx is None:
                from sarr.api.onnx_embedder import OnnxQueryEmbedder

                self._onnx = OnnxQueryEmbedder(onnx_dir)
            return self._onnx.embed(text)

        vector = self.model.encode(text, normalize_embeddings=True)
        return np.asarray(vector, dtype=np.float32).tolist()

    @property
    def model(self) -> SentenceTransformer:
        if self._model is None:
            logger.info("importing torch for embedder")
            import torch

            logger.info("torch %s cuda=%s", torch.__version__, torch.cuda.is_available())
            logger.info("importing sentence_transformers for embedder")
            from sentence_transformers import SentenceTransformer

            source = resolve_model_source(self.model_name)
            logger.info("loading embedder from %s", source)
            kwargs: dict[str, bool] = {}
            if Path(source).is_dir():
                kwargs["local_files_only"] = True
            self._model = SentenceTransformer(source, **kwargs)
        return self._model
