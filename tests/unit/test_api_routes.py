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


@pytest.mark.unit
def test_rag_endpoint_streams_ranked_list_then_llm(monkeypatch: pytest.MonkeyPatch) -> None:
    import json as json_lib

    from sarr.api.rag_service import RagService
    from sarr.api.search_service import SearchService
    from sarr.common.config import Settings

    class Embedder:
        def embed(self, text: str) -> list[float]:
            return [0.1, 0.2, 0.3]

    class Store:
        def search(self, vector, *, limit: int, query_filter=None):
            return [
                {
                    "id": "requests",
                    "score": 0.91,
                    "payload": {"name": "requests", "summary": "HTTP for Humans", "stars": 1},
                },
                {
                    "id": "httpx",
                    "score": 0.88,
                    "payload": {"name": "httpx", "summary": "HTTP client", "stars": 1},
                },
            ][:limit]

    class Reranker:
        def rerank(self, query: str, documents: list[str]) -> list[float]:
            return [0.1, 0.9][: len(documents)]

    class Llm:
        def configured(self) -> bool:
            return True

        def stream(self, prompt: str):
            payload = {
                "recommendations": [
                    {
                        "package": "httpx",
                        "reason": "HTTP client.",
                        "cited_snippet": "HTTP client",
                    },
                    {
                        "package": "requests",
                        "reason": "HTTP for humans.",
                        "cited_snippet": "HTTP for Humans",
                    },
                    {
                        "package": "aiohttp",
                        "reason": "Not in this stub store.",
                        "cited_snippet": "async",
                    },
                ]
            }
            yield json_lib.dumps(payload)

    search = SearchService(
        settings=Settings(rag_context_k=8, rag_retrieve_k=50, rag_rerank_k=50),
        embedder=Embedder(),  # type: ignore[arg-type]
        vector_store=Store(),  # type: ignore[arg-type]
        reranker=Reranker(),  # type: ignore[arg-type]
        warm=False,
    )
    monkeypatch.setattr(routes, "_service", search)
    monkeypatch.setattr(
        routes,
        "_rag_service",
        RagService(search, settings=search.settings, llm=Llm()),
    )
    app = create_app()
    client = TestClient(app)
    with client.stream("POST", "/v1/rag", json={"query": "http client"}) as response:
        assert response.status_code == 200
        assert "text/event-stream" in response.headers["content-type"]
        body = "".join(response.iter_text())
    assert "event: ranked_list" in body
    assert "event: llm_delta" in body
    assert "event: llm_done" in body
    assert "aiohttp" in body
