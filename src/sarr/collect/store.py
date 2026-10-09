"""Qdrant payload access for the collector (kept out of sarr.api, which imports FastAPI)."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from sarr.common.config import Settings


class QdrantPayloadStore:
    def __init__(self, settings: Settings) -> None:
        from qdrant_client import QdrantClient

        self.collection = settings.qdrant_collection
        self.client = QdrantClient(
            url=settings.qdrant_url, api_key=settings.qdrant_api_key, timeout=60
        )

    def set_payload(self, point_id: str, payload: dict[str, Any]) -> None:
        """Partial payload update: merges keys into the point, no re-embedding."""
        self.client.set_payload(
            collection_name=self.collection,
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
                collection_name=self.collection,
                limit=batch_size,
                offset=offset,
                with_payload=fields,
                with_vectors=False,
            )
            for point in points:
                yield str(point.id), point.payload or {}
            if offset is None:
                return
