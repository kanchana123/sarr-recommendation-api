"""Cross-encoder reranker for top-k candidates."""

from __future__ import annotations

from typing import TYPE_CHECKING

from sarr.common.config import get_settings

if TYPE_CHECKING:
    from sentence_transformers import CrossEncoder


class Reranker:
    def __init__(self, model_name: str | None = None) -> None:
        self.model_name = model_name or get_settings().reranker_model
        self._model: CrossEncoder | None = None

    @property
    def model(self) -> CrossEncoder:
        if self._model is None:
            from sentence_transformers import CrossEncoder

            self._model = CrossEncoder(self.model_name)
        return self._model

    def rerank(self, query: str, documents: list[str]) -> list[float]:
        if not documents:
            return []
        pairs = [(query, doc) for doc in documents]
        scores = self.model.predict(pairs)
        return [float(score) for score in scores]
