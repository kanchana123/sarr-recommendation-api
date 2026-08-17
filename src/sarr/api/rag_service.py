"""Retrieve → rerank → emit ranked_list, then stream a grounded Gemini top-3."""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Iterator
from typing import Any

from sarr.api.gemini_client import TokenStreamer, VertexGeminiStreamer
from sarr.api.rag_prompt import build_prompt
from sarr.api.rag_validate import validate_recommendations
from sarr.api.search_service import SearchService
from sarr.common.config import Settings, get_settings
from sarr.common.schemas import RagRequest, SearchRequest, SearchResponse

logger = logging.getLogger("sarr.api")


def format_sse(event: str, data: Any) -> str:
    payload = json.dumps(data, default=str)
    return f"event: {event}\ndata: {payload}\n\n"


class RagService:
    def __init__(
        self,
        search_service: SearchService,
        settings: Settings | None = None,
        llm: TokenStreamer | None = None,
    ) -> None:
        self.search_service = search_service
        self.settings = settings or get_settings()
        self.llm = llm or VertexGeminiStreamer(self.settings)

    def retrieve(self, request: RagRequest) -> SearchResponse:
        started = time.perf_counter()
        search_request = SearchRequest(
            query=request.query,
            limit=self.settings.rag_context_k,
            rerank=True,
            filters=request.filters,
        )
        ranked = self.search_service.search(
            search_request,
            retrieve_k=self.settings.rag_retrieve_k,
            rerank_k=self.settings.rag_rerank_k,
            output_limit=self.settings.rag_context_k,
        )
        ranked.took_ms = round((time.perf_counter() - started) * 1000.0, 1)
        return ranked

    def stream(self, request: RagRequest) -> Iterator[str]:
        ranked = self.retrieve(request)
        yield from self.stream_generation(request.query, ranked)

    def stream_generation(self, query: str, ranked: SearchResponse) -> Iterator[str]:
        yield format_sse(
            "ranked_list",
            ranked.model_dump(mode="json"),
        )

        if not ranked.results:
            yield format_sse(
                "llm_error",
                {"message": "No packages were retrieved for this query."},
            )
            return

        configured = getattr(self.llm, "configured", None)
        if callable(configured) and not configured():
            yield format_sse(
                "llm_error",
                {
                    "message": (
                        "Vertex AI is not configured (set GCP_PROJECT_ID). "
                        "The ranked list above is complete without generation."
                    )
                },
            )
            return

        prompt = build_prompt(query, ranked.results)
        accumulated = ""
        llm_started = time.perf_counter()
        try:
            for token in self.llm.stream(prompt):
                accumulated += token
                yield format_sse("llm_delta", {"text": token})
                elapsed = time.perf_counter() - llm_started
                if elapsed > self.settings.rag_llm_timeout_s:
                    raise TimeoutError(
                        f"LLM exceeded {self.settings.rag_llm_timeout_s:.0f}s"
                    )
        except Exception as exc:  # noqa: BLE001 — ranked_list already sent
            logger.exception("rag llm failed query=%r error=%s", query, exc)
            yield format_sse("llm_error", {"message": str(exc)})
            return

        result = validate_recommendations(accumulated, ranked.results)
        yield format_sse(
            "llm_done",
            {
                **result.model_dump(mode="json"),
                "llm_ms": round((time.perf_counter() - llm_started) * 1000.0, 1),
                "model": self.settings.vertex_gemini_model,
            },
        )
