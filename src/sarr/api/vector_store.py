"""Qdrant client wrapper for online search."""

from __future__ import annotations

from typing import Any

from sarr.common.config import Settings, get_settings


class VectorStore:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self._client: Any | None = None

    @property
    def client(self) -> Any:
        if self._client is None:
            from qdrant_client import QdrantClient

            self._client = QdrantClient(
                url=self.settings.qdrant_url,
                api_key=self.settings.qdrant_api_key,
            )
        return self._client

    def search(
        self,
        vector: list[float],
        *,
        limit: int,
        query_filter: dict[str, Any] | None = None,
        vectors_only: bool | None = None,
    ) -> list[dict[str, Any]]:
        """ANN search. In vectors-only mode, returns scores + package name (no rich metadata)."""
        from qdrant_client.http import models as qmodels

        qdrant_filter = None
        if query_filter:
            qdrant_filter = qmodels.Filter.model_validate(query_filter)

        use_vectors_only = (
            self.settings.qdrant_vectors_only if vectors_only is None else vectors_only
        )
        if use_vectors_only:
            # Identity only — full metadata is loaded from BigQuery after retrieval.
            payload_selector: bool | qmodels.PayloadSelectorInclude = (
                qmodels.PayloadSelectorInclude(include=["name"])
            )
        else:
            payload_selector = True

        response = self.client.query_points(
            collection_name=self.settings.qdrant_collection,
            query=vector,
            limit=limit,
            query_filter=qdrant_filter,
            with_payload=payload_selector,
        )
        results: list[dict[str, Any]] = []
        for hit in response.points:
            results.append(
                {
                    "id": hit.id,
                    "score": float(hit.score),
                    "payload": hit.payload or {},
                }
            )
        return results
