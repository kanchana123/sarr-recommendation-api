"""HTTP client helpers for the SARR MCP server."""

from __future__ import annotations

import json
import os
from typing import Any

import httpx

from sarr.common.schemas import SearchRequest, SearchResponse

DEFAULT_API_URL = "http://localhost:8080"
DEFAULT_SEARCH_TIMEOUT_S = 120.0
DEFAULT_RAG_TIMEOUT_S = 90.0


def resolve_api_url() -> str:
    env_url = os.environ.get("SARR_API_URL", "").strip()
    if env_url:
        return env_url.rstrip("/")
    return DEFAULT_API_URL


def search_packages(
    query: str,
    *,
    limit: int = 10,
    rerank: bool = False,
    api_url: str | None = None,
    timeout: float = DEFAULT_SEARCH_TIMEOUT_S,
) -> dict[str, Any]:
    """Call POST /v1/search and return agent-friendly JSON."""
    base = (api_url or resolve_api_url()).rstrip("/")
    request = SearchRequest(query=query, limit=limit, rerank=rerank)
    with httpx.Client(timeout=timeout) as client:
        response = client.post(
            f"{base}/v1/search",
            json=request.model_dump(exclude_none=True),
        )
        response.raise_for_status()
        payload = SearchResponse.model_validate(response.json())
    return _search_payload(payload)


def health_check(*, api_url: str | None = None, timeout: float = 15.0) -> dict[str, Any]:
    base = (api_url or resolve_api_url()).rstrip("/")
    with httpx.Client(timeout=timeout) as client:
        response = client.get(f"{base}/healthz")
        response.raise_for_status()
        body = response.json()
    return {"status": body.get("status", "unknown"), "api_url": base}


def recommend_packages(
    query: str,
    *,
    rerank: bool = True,
    include_ranked_list: bool = True,
    api_url: str | None = None,
    timeout: float = DEFAULT_RAG_TIMEOUT_S,
) -> dict[str, Any]:
    """Call POST /v1/rag, consume SSE, return one JSON blob for agents."""
    base = (api_url or resolve_api_url()).rstrip("/")
    ranked_data: dict[str, Any] | None = None
    recommendations: list[dict[str, Any]] = []
    dropped: list[str] = []
    llm_ms: float | None = None
    error: str | None = None
    reranked: bool | None = None

    try:
        with httpx.Client(timeout=timeout) as client:
            with client.stream(
                "POST",
                f"{base}/v1/rag",
                json={"query": query, "rerank": rerank},
                headers={"Accept": "text/event-stream"},
            ) as response:
                if response.status_code == 404:
                    return _rag_unavailable(base)
                response.raise_for_status()
                buffer = ""
                for chunk in response.iter_text():
                    if not chunk:
                        continue
                    buffer += chunk
                    buffer, events = _take_sse_events(buffer)
                    for event, data in events:
                        if event == "ranked_list":
                            ranked_data = data
                            reranked = data.get("reranked")
                        elif event == "llm_done":
                            recommendations = data.get("recommendations") or []
                            dropped = data.get("dropped") or []
                            llm_ms = data.get("llm_ms")
                        elif event == "llm_error":
                            error = data.get("message") or "Generation failed."
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 404:
            return _rag_unavailable(base)
        raise

    ranked = [_slim_hit(hit) for hit in (ranked_data or {}).get("results") or []]
    fast_path_ms = (ranked_data or {}).get("took_ms")

    result: dict[str, Any] = {
        "query": query,
        "recommendations": recommendations,
        "dropped": dropped,
        "reranked": reranked,
        "fast_path_ms": fast_path_ms,
        "llm_ms": llm_ms,
        "error": error,
    }
    if include_ranked_list:
        result["ranked"] = ranked
        result["ranked_total"] = (ranked_data or {}).get("total", len(ranked))
    return result


def _rag_unavailable(api_url: str) -> dict[str, Any]:
    return {
        "query": "",
        "ranked": [],
        "ranked_total": 0,
        "recommendations": [],
        "dropped": [],
        "reranked": None,
        "fast_path_ms": None,
        "llm_ms": None,
        "error": (
            f"RAG is not available at {api_url}/v1/rag. "
            "Run the API locally (make run-api) or deploy a stack with /v1/rag."
        ),
    }


def _search_payload(response: SearchResponse) -> dict[str, Any]:
    return {
        "query": response.query,
        "total": response.total,
        "reranked": response.reranked,
        "took_ms": response.took_ms,
        "results": [_slim_hit(hit.model_dump()) for hit in response.results],
    }


def _slim_hit(hit: dict[str, Any]) -> dict[str, Any]:
    return {
        "name": hit.get("name"),
        "summary": hit.get("summary"),
        "score": hit.get("score"),
        "stars": hit.get("stars", 0),
        "pypi_url": hit.get("pypi_url"),
    }


def _take_sse_events(buffer: str) -> tuple[str, list[tuple[str, dict[str, Any]]]]:
    events: list[tuple[str, dict[str, Any]]] = []
    while "\n\n" in buffer:
        block, buffer = buffer.split("\n\n", 1)
        block = block.strip()
        if not block or block.startswith(":"):
            continue
        event = "message"
        data_lines: list[str] = []
        for line in block.split("\n"):
            if line.startswith("event:"):
                event = line[6:].strip()
            elif line.startswith("data:"):
                data_lines.append(line[5:].strip())
        if not data_lines:
            continue
        data = json.loads("\n".join(data_lines))
        events.append((event, data))
    return buffer, events
