"""HTTP routes for health and search."""

from __future__ import annotations

import logging
import os
import time

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

from sarr.api.rag_service import RagService
from sarr.api.search_service import SearchService
from sarr.common.schemas import RagRequest, SearchRequest, SearchResponse

logger = logging.getLogger("sarr.api")

router = APIRouter()
_service: SearchService | None = None
_rag_service: RagService | None = None


def get_search_service() -> SearchService:
    global _service
    if _service is None:
        # Skip extra warmup encode on Lambda; first search already loads the model.
        warm = "AWS_LAMBDA_FUNCTION_NAME" not in os.environ
        _service = SearchService(warm=warm)
    return _service


def get_rag_service() -> RagService:
    global _rag_service
    if _rag_service is None:
        _rag_service = RagService(get_search_service())
    return _rag_service


@router.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}


@router.post("/v1/search", response_model=SearchResponse)
def search(request: SearchRequest) -> SearchResponse:
    started = time.perf_counter()
    try:
        response = get_search_service().search(request)
    except Exception as exc:  # noqa: BLE001 — surfaced as HTTP 502 for MVP
        took_ms = (time.perf_counter() - started) * 1000.0
        logger.exception(
            "search failed query=%r rerank=%s took_ms=%.1f error=%s",
            request.query,
            request.rerank,
            took_ms,
            exc,
        )
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    took_ms = (time.perf_counter() - started) * 1000.0
    response.took_ms = round(took_ms, 1)
    timing = response.timing_ms or {}
    logger.info(
        "search ok query=%r limit=%s rerank=%s total=%s took_ms=%.1f "
        "embed_ms=%s qdrant_ms=%s rerank_ms=%s blend_ms=%s",
        request.query,
        request.limit,
        response.reranked,
        response.total,
        response.took_ms,
        timing.get("embed_ms"),
        timing.get("qdrant_ms"),
        timing.get("rerank_ms"),
        timing.get("blend_ms"),
    )
    return response


@router.post("/v1/rag")
def rag(request: RagRequest) -> StreamingResponse:
    """SSE: ranked_list first, then llm_delta tokens, then llm_done or llm_error."""
    service = get_rag_service()
    try:
        ranked = service.retrieve(request)
    except Exception as exc:  # noqa: BLE001 — surfaced as HTTP 502 for MVP
        logger.exception("rag retrieve failed query=%r error=%s", request.query, exc)
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    def events() -> object:
        yield from service.stream_generation(request.query, ranked)

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
