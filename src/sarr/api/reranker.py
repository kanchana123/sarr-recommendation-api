"""Cross-encoder reranker for top-k candidates."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from sarr.api.model_source import resolve_model_source
from sarr.api.onnx_embedder import onnx_model_dir
from sarr.common.config import get_settings

if TYPE_CHECKING:
    from sentence_transformers import CrossEncoder

logger = logging.getLogger("sarr.api")


class Reranker:
    def __init__(self, model_name: str | None = None) -> None:
        self.model_name = model_name or get_settings().reranker_model
        self._model: CrossEncoder | None = None
        self._onnx: Any | None = None

    def rerank(self, query: str, documents: list[str]) -> list[float]:
        if not documents:
            return []
        onnx_dir = onnx_model_dir(self.model_name)
        if onnx_dir is not None:
            if self._onnx is None:
                from sarr.api.onnx_reranker import OnnxCrossEncoder

                self._onnx = OnnxCrossEncoder(onnx_dir)
            pairs = [(query, doc) for doc in documents]
            return self._onnx.predict(pairs)

        pairs = [(query, doc) for doc in documents]
        scores = self.model.predict(pairs)
        return [float(score) for score in scores]

    @property
    def model(self) -> CrossEncoder:
        if self._model is None:
            from sentence_transformers import CrossEncoder

            source = resolve_model_source(self.model_name)
            logger.info("loading reranker from %s", source)
            self._model = CrossEncoder(source)
        return self._model
