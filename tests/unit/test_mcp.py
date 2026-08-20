"""Unit tests for MCP SSE parsing and RAG result shaping."""

from __future__ import annotations

import json

import pytest

from sarr.mcp.api_client import _take_sse_events, recommend_packages


@pytest.mark.unit
def test_take_sse_events_parses_blocks() -> None:
    raw = (
        'event: ranked_list\n'
        'data: {"total": 2, "results": [{"name": "requests"}]}\n\n'
        'event: llm_done\n'
        'data: {"recommendations": [{"package": "requests"}], "llm_ms": 900}\n\n'
    )
    buffer, events = _take_sse_events(raw)
    assert buffer == ""
    assert len(events) == 2
    assert events[0][0] == "ranked_list"
    assert events[0][1]["total"] == 2
    assert events[1][0] == "llm_done"
    assert events[1][1]["llm_ms"] == 900


@pytest.mark.unit
def test_take_sse_events_handles_partial_buffer() -> None:
    buffer, events = _take_sse_events("event: ping\ndata: {}\n")
    assert events == []
    assert "event: ping" in buffer


@pytest.mark.unit
def test_recommend_packages_maps_sse_to_json(monkeypatch: pytest.MonkeyPatch) -> None:
    sse_body = (
        'event: ranked_list\n'
        + "data: "
        + json.dumps(
            {
                "total": 1,
                "reranked": True,
                "took_ms": 420.0,
                "results": [
                    {
                        "name": "httpx",
                        "summary": "HTTP client",
                        "score": 0.9,
                        "stars": 12000,
                        "pypi_url": "https://pypi.org/project/httpx/",
                    }
                ],
            }
        )
        + "\n\n"
        'event: llm_done\n'
        + "data: "
        + json.dumps(
            {
                "recommendations": [
                    {
                        "package": "httpx",
                        "reason": "Modern async HTTP",
                        "cited_snippet": "HTTP client",
                        "snippet_in_description": True,
                    }
                ],
                "dropped": [],
                "llm_ms": 1050.0,
            }
        )
        + "\n\n"
    )

    class FakeStream:
        status_code = 200

        def raise_for_status(self) -> None:
            return None

        def iter_text(self):
            yield sse_body

    class FakeStreamCtx:
        def __enter__(self):
            return FakeStream()

        def __exit__(self, *_args):
            return None

    class FakeClient:
        def stream(self, *_args, **_kwargs):
            return FakeStreamCtx()

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

    monkeypatch.setattr("httpx.Client", lambda **_kw: FakeClient())

    out = recommend_packages("async HTTP client", api_url="http://test")
    assert out["fast_path_ms"] == 420.0
    assert out["llm_ms"] == 1050.0
    assert out["error"] is None
    assert out["ranked"][0]["name"] == "httpx"
    assert out["recommendations"][0]["package"] == "httpx"


@pytest.mark.unit
def test_recommend_packages_llm_error_still_returns_ranked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sse_body = (
        'event: ranked_list\n'
        + "data: "
        + json.dumps(
            {
                "total": 1,
                "reranked": False,
                "took_ms": 30.0,
                "results": [{"name": "requests", "summary": "HTTP", "score": 0.8}],
            }
        )
        + "\n\n"
        'event: llm_error\n'
        + 'data: {"message": "Vertex AI is not configured"}\n\n'
    )

    class FakeStream:
        status_code = 200

        def raise_for_status(self) -> None:
            return None

        def iter_text(self):
            yield sse_body

    class FakeStreamCtx:
        def __enter__(self):
            return FakeStream()

        def __exit__(self, *_args):
            return None

    class FakeClient:
        def stream(self, *_a, **_k):
            return FakeStreamCtx()

        def __enter__(self):
            return self

        def __exit__(self, *_a):
            return None

    monkeypatch.setattr("httpx.Client", lambda **_kw: FakeClient())

    out = recommend_packages("http client", api_url="http://test")
    assert out["ranked"][0]["name"] == "requests"
    assert out["error"] == "Vertex AI is not configured"
    assert out["recommendations"] == []
