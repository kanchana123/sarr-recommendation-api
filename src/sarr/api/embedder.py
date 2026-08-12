"""Query embedding for online search (Lambda / local API)."""

from __future__ import annotations

from typing import TYPE_CHECKING

import numpy as np

from sarr.common.config import get_settings

if TYPE_CHECKING:
    from sentence_transformers import SentenceTransformer


class QueryEmbedder:
    """Lazy-loads the bi-encoder once per process (warm Lambda reuse)."""

    def __init__(self, model_name: str | None = None) -> None:
        self.model_name = model_name or get_settings().embedding_model
        self._model: SentenceTransformer | None = None

    @property
    def model(self) -> SentenceTransformer:
        if self._model is None:
            from sentence_transformers import SentenceTransformer

            self._model = SentenceTransformer(self.model_name)
        return self._model

    def embed(self, text: str) -> list[float]:
        vector = self.model.encode(text, normalize_embeddings=True)
        return np.asarray(vector, dtype=np.float32).tolist()
