"""Load / upsert points into Qdrant Cloud."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from sarr.common.config import Settings, get_settings

# Qdrant rejects filters on unindexed payload keys; keep in sync with
# SearchService._build_filter.
PAYLOAD_INDEXES: dict[str, str] = {
    "stars": "integer",
    "license": "keyword",
    "requires_python": "keyword",
}


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
        if self.settings.qdrant_collection not in names:
            self.client.create_collection(
                collection_name=self.settings.qdrant_collection,
                vectors_config=qmodels.VectorParams(
                    size=self.settings.embedding_dim,
                    distance=qmodels.Distance.COSINE,
                ),
            )
        self.ensure_payload_indexes()

    def ensure_payload_indexes(self) -> list[str]:
        """Create any missing filter indexes; returns the keys that were created."""
        from qdrant_client.http import models as qmodels

        info = self.client.get_collection(self.settings.qdrant_collection)
        existing = set((info.payload_schema or {}).keys())
        created: list[str] = []
        for key, schema in PAYLOAD_INDEXES.items():
            if key in existing:
                continue
            self.client.create_payload_index(
                collection_name=self.settings.qdrant_collection,
                field_name=key,
                field_schema=qmodels.PayloadSchemaType(schema),
                wait=True,
            )
            created.append(key)
        return created

    def upsert_batch(
        self,
        ids: Sequence[str],
        vectors: Sequence[Sequence[float]],
        payloads: Sequence[dict[str, Any]],
    ) -> None:
        from qdrant_client.http import models as qmodels

        points = [
            qmodels.PointStruct(id=self._point_id(pid), vector=list(vector), payload=payload)
            for pid, vector, payload in zip(ids, vectors, payloads, strict=True)
        ]
        self.client.upsert(
            collection_name=self.settings.qdrant_collection,
            points=points,
            wait=True,
        )

    @staticmethod
    def _point_id(package_name: str) -> str:
        # Qdrant accepts UUID or unsigned int; use UUID5 derived from name for stability
        import uuid

        return str(uuid.uuid5(uuid.NAMESPACE_DNS, f"pypi:{package_name}"))
