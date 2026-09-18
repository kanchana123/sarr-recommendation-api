"""Unit tests for Qdrant loader (vectors-only payloads)."""

from __future__ import annotations

from typing import Any

import pytest

from sarr.common.config import Settings
from sarr.common.point_id import package_point_id
from sarr.etl.load import QdrantLoader


class FakeQdrantClient:
    def __init__(self) -> None:
        self.upserts: list[tuple[str, list[Any]]] = []

    def upsert(self, *, collection_name: str, points: list[Any], wait: bool) -> None:
        self.upserts.append((collection_name, points))


@pytest.mark.unit
def test_upsert_vectors_only_stores_name_payload_only(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakeQdrantClient()
    settings = Settings(qdrant_vectors_only=True, qdrant_collection="test-col")

    loader = QdrantLoader(settings)
    monkeypatch.setattr(loader, "_client", fake)

    loader.upsert_batch(
        ["Requests"],
        [[0.1, 0.2]],
        [{"name": "requests", "summary": "should not be stored", "stars": 99}],
    )

    assert len(fake.upserts) == 1
    collection, points = fake.upserts[0]
    assert collection == "test-col"
    assert len(points) == 1
    point = points[0]
    assert str(point.id) == package_point_id("requests")
    assert point.payload == {"name": "requests"}
    assert "summary" not in (point.payload or {})


@pytest.mark.unit
def test_upsert_full_payload_when_not_vectors_only(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakeQdrantClient()
    settings = Settings(qdrant_vectors_only=False, qdrant_collection="test-col")
    loader = QdrantLoader(settings)
    monkeypatch.setattr(loader, "_client", fake)

    payload = {"name": "requests", "summary": "HTTP for Humans.", "stars": 1}
    loader.upsert_batch(["requests"], [[0.1]], [payload])

    _, points = fake.upserts[0]
    assert points[0].payload == payload
