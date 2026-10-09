"""Qdrant client wrapper for online search."""

from __future__ import annotations

from collections.abc import Iterator
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
    ) -> list[dict[str, Any]]:
        # qdrant-client >= 1.16 removed Client.search in favor of query_points
        from qdrant_client.http import models as qmodels

        qdrant_filter = None
        if query_filter:
            qdrant_filter = qmodels.Filter.model_validate(query_filter)

        response = self.client.query_points(
            collection_name=self.settings.qdrant_collection,
            query=vector,
            limit=limit,
            query_filter=qdrant_filter,
            with_payload=True,
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

    def set_payload(self, point_id: str, payload: dict[str, Any]) -> None:
        """Partial payload update: merges keys into the point, no re-embedding."""
        self.client.set_payload(
            collection_name=self.settings.qdrant_collection,
            payload=payload,
            points=[point_id],
            wait=True,
        )

    def scroll_payloads(
        self, fields: list[str], *, batch_size: int = 5000
    ) -> Iterator[tuple[str, dict[str, Any]]]:
        offset = None
        while True:
            points, offset = self.client.scroll(
                collection_name=self.settings.qdrant_collection,
                limit=batch_size,
                offset=offset,
                with_payload=fields,
                with_vectors=False,
            )
            for point in points:
                yield str(point.id), point.payload or {}
            if offset is None:
                return
