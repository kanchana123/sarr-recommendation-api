"""Load / upsert points into Qdrant Cloud."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from sarr.common.config import Settings, get_settings
from sarr.common.point_id import package_point_id


class QdrantLoader:
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

    def ensure_collection(self) -> None:
        from qdrant_client.http import models as qmodels

        names = {c.name for c in self.client.get_collections().collections}
        if self.settings.qdrant_collection in names:
            return
        self.client.create_collection(
            collection_name=self.settings.qdrant_collection,
            vectors_config=qmodels.VectorParams(
                size=self.settings.embedding_dim,
                distance=qmodels.Distance.COSINE,
            ),
        )

    def upsert_batch(
        self,
        ids: Sequence[str],
        vectors: Sequence[Sequence[float]],
        payloads: Sequence[dict[str, Any]] | None = None,
    ) -> None:
        from qdrant_client.http import models as qmodels

        # Vectors-only index: store package name for identity; metadata lives in BigQuery.
        qdrant_payloads: list[dict[str, Any] | None]
        if self.settings.qdrant_vectors_only:
            qdrant_payloads = [
                {"name": pid.strip().lower().replace("_", "-")} for pid in ids
            ]
        else:
            qdrant_payloads = list(payloads or [{} for _ in ids])

        points = [
            qmodels.PointStruct(
                id=package_point_id(pid),
                vector=list(vector),
                payload=payload,
            )
            for pid, vector, payload in zip(ids, vectors, qdrant_payloads, strict=True)
        ]
        self.client.upsert(
            collection_name=self.settings.qdrant_collection,
            points=points,
            wait=True,
        )
