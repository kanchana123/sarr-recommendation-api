"""Batch embedding for ETL (Colab GPU / local)."""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING

import numpy as np

from sarr.common.config import get_settings

if TYPE_CHECKING:
    from sentence_transformers import SentenceTransformer


class BatchEmbedder:
    def __init__(self, model_name: str | None = None, device: str | None = None) -> None:
        self.model_name = model_name or get_settings().embedding_model
        self.device = device
        self._model: SentenceTransformer | None = None

    @property
    def model(self) -> SentenceTransformer:
        if self._model is None:
            from sentence_transformers import SentenceTransformer

            kwargs = {}
            if self.device:
                kwargs["device"] = self.device
            self._model = SentenceTransformer(self.model_name, **kwargs)
        return self._model

    def embed_documents(self, documents: Sequence[str], batch_size: int = 64) -> list[list[float]]:
        if not documents:
            return []
        vectors = self.model.encode(
            list(documents),
            batch_size=batch_size,
            normalize_embeddings=True,
            show_progress_bar=False,
        )
        return np.asarray(vectors, dtype=np.float32).tolist()
