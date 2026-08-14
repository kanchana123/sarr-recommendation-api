"""API route tests with dependency overrides / stubbed service."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from sarr.api import routes
from sarr.api.app import create_app
from sarr.common.schemas import SearchHit, SearchRequest, SearchResponse


class StubSearchService:
    def search(self, request: SearchRequest) -> SearchResponse:
        return SearchResponse(
            query=request.query,
            total=1,
            reranked=False,
            results=[
                SearchHit(
                    name="requests",
                    summary="HTTP for Humans",
                    score=0.95,
                    stars=52000,
                )
            ],
            timing_ms={"embed_ms": 1.0, "qdrant_ms": 2.0, "rerank_ms": 0.0, "blend_ms": 0.1},
        )


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setattr(routes, "_service", StubSearchService())
    app = create_app()
    return TestClient(app)


@pytest.mark.unit
def test_healthz(client: TestClient) -> None:
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


@pytest.mark.unit
def test_search_endpoint(client: TestClient) -> None:
    response = client.post("/v1/search", json={"query": "http library", "limit": 5})
    assert response.status_code == 200
    body = response.json()
    assert body["query"] == "http library"
    assert body["results"][0]["name"] == "requests"
    assert "took_ms" in body
    assert isinstance(body["took_ms"], (int, float))
