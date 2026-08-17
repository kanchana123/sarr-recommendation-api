"""RAG SSE orchestration with stubbed retrieval and LLM."""

from __future__ import annotations

import json

import pytest

from sarr.api.rag_service import RagService, format_sse
from sarr.api.search_service import SearchService
from sarr.common.config import Settings
from sarr.common.schemas import RagRequest


class FakeEmbedder:
    def embed(self, text: str) -> list[float]:
        return [0.1, 0.2, 0.3]


class FakeStore:
    def search(self, vector, *, limit: int, query_filter=None):
        hits = [
            {
                "id": "requests",
                "score": 0.91,
                "payload": {
                    "name": "requests",
                    "summary": "HTTP for Humans",
                    "stars": 52000,
                    "forks": 100,
                },
            },
            {
                "id": "httpx",
                "score": 0.88,
                "payload": {"name": "httpx", "summary": "HTTP client", "stars": 12000},
            },
        ]
        return hits[:limit]


class FakeReranker:
    def rerank(self, query: str, documents: list[str]) -> list[float]:
        return [0.1, 0.9][: len(documents)]


class FakeLlm:
    def __init__(self, payload: dict, *, configured: bool = True) -> None:
        self._payload = payload
        self._configured = configured
        self.prompts: list[str] = []

    def configured(self) -> bool:
        return self._configured

    def stream(self, prompt: str):
        self.prompts.append(prompt)
        raw = json.dumps(self._payload)
        yield raw[:24]
        yield raw[24:]


def _service(llm: FakeLlm) -> RagService:
    search = SearchService(
        settings=Settings(
            rerank_enabled_default=False,
            search_top_k=50,
            rag_retrieve_k=50,
            rag_rerank_k=50,
            rag_context_k=8,
        ),
        embedder=FakeEmbedder(),  # type: ignore[arg-type]
        vector_store=FakeStore(),  # type: ignore[arg-type]
        reranker=FakeReranker(),  # type: ignore[arg-type]
        warm=False,
    )
    return RagService(search, settings=search.settings, llm=llm)


def _parse_events(chunks: list[str]) -> list[tuple[str, dict]]:
    events: list[tuple[str, dict]] = []
    for chunk in chunks:
        event = "message"
        data = None
        for line in chunk.strip().split("\n"):
            if line.startswith("event:"):
                event = line.split(":", 1)[1].strip()
            elif line.startswith("data:"):
                data = json.loads(line.split(":", 1)[1].strip())
        if data is not None:
            events.append((event, data))
    return events


@pytest.mark.unit
def test_format_sse_is_event_stream() -> None:
    block = format_sse("ranked_list", {"total": 2})
    assert block.startswith("event: ranked_list\n")
    assert block.endswith("\n\n")


@pytest.mark.unit
def test_stream_emits_ranked_list_before_llm_and_drops_hallucinations() -> None:
    llm = FakeLlm(
        {
            "recommendations": [
                {
                    "package": "httpx",
                    "reason": "HTTP client with a clean API.",
                    "cited_snippet": "HTTP client",
                },
                {
                    "package": "ghost-pkg",
                    "reason": "Not retrieved.",
                    "cited_snippet": "nope",
                },
                {
                    "package": "requests",
                    "reason": "Popular HTTP library.",
                    "cited_snippet": "HTTP for Humans",
                },
            ]
        }
    )
    service = _service(llm)
    events = _parse_events(list(service.stream(RagRequest(query="http client"))))
    names = [name for name, _ in events]
    assert names[0] == "ranked_list"
    assert "llm_delta" in names
    assert names[-1] == "llm_done"
    ranked = events[0][1]
    assert ranked["reranked"] is True
    assert len(ranked["results"]) == 2
    done = events[-1][1]
    assert [rec["package"] for rec in done["recommendations"]] == ["httpx", "requests"]
    assert done["dropped"] == ["ghost-pkg"]
    assert "stars" not in llm.prompts[0]


@pytest.mark.unit
def test_unconfigured_llm_still_returns_ranked_list() -> None:
    service = _service(FakeLlm({"recommendations": []}, configured=False))
    events = _parse_events(list(service.stream(RagRequest(query="http client"))))
    assert events[0][0] == "ranked_list"
    assert events[-1][0] == "llm_error"
