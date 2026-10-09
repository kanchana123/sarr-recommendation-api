"""QdrantLoader collection and payload index setup against a fake client."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from sarr.api.search_service import SearchService
from sarr.common.config import Settings
from sarr.common.schemas import SearchFilters, SearchRequest
from sarr.etl.load import PAYLOAD_INDEXES, QdrantLoader


class FakeQdrantClient:
    def __init__(self, collections: list[str], payload_schema: dict[str, Any]) -> None:
        self.collections = collections
        self.payload_schema = payload_schema
        self.created_collections: list[str] = []
        self.created_indexes: list[tuple[str, str]] = []

    def get_collections(self) -> Any:
        return SimpleNamespace(collections=[SimpleNamespace(name=n) for n in self.collections])

    def create_collection(self, collection_name: str, vectors_config: Any) -> None:
        self.created_collections.append(collection_name)
        self.collections.append(collection_name)

    def get_collection(self, collection_name: str) -> Any:
        return SimpleNamespace(payload_schema=self.payload_schema)

    def create_payload_index(
        self, collection_name: str, field_name: str, field_schema: Any, wait: bool
    ) -> None:
        self.created_indexes.append((field_name, field_schema.value))
        self.payload_schema[field_name] = field_schema


def _loader(client: FakeQdrantClient) -> QdrantLoader:
    loader = QdrantLoader(Settings(qdrant_collection="sarr"))
    loader._client = client
    return loader


@pytest.mark.unit
def test_ensure_collection_creates_collection_and_indexes() -> None:
    client = FakeQdrantClient(collections=[], payload_schema={})
    _loader(client).ensure_collection()
    assert client.created_collections == ["sarr"]
    assert sorted(client.created_indexes) == sorted(PAYLOAD_INDEXES.items())


@pytest.mark.unit
def test_ensure_collection_indexes_existing_collection() -> None:
    client = FakeQdrantClient(collections=["sarr"], payload_schema={})
    _loader(client).ensure_collection()
    assert client.created_collections == []
    assert {key for key, _ in client.created_indexes} == set(PAYLOAD_INDEXES)


@pytest.mark.unit
def test_ensure_payload_indexes_only_creates_missing() -> None:
    client = FakeQdrantClient(collections=["sarr"], payload_schema={"stars": object()})
    created = _loader(client).ensure_payload_indexes()
    assert set(created) == {"license", "requires_python"}
    assert _loader(client).ensure_payload_indexes() == []


@pytest.mark.unit
def test_every_filter_key_has_a_payload_index() -> None:
    request = SearchRequest(
        query="orm",
        filters=SearchFilters(min_stars=1, license="MIT", requires_python=">=3.8"),
    )
    query_filter = SearchService._build_filter(object(), request)  # type: ignore[arg-type]
    assert query_filter is not None
    assert {cond["key"] for cond in query_filter["must"]} <= set(PAYLOAD_INDEXES)
