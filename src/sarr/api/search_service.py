"""Orchestrates query embed → ANN → optional rerank → score blend."""

from __future__ import annotations

import logging
import time
from typing import Any

from sarr.api.embedder import QueryEmbedder
from sarr.api.ranking import blend_scores
from sarr.api.reranker import Reranker
from sarr.api.vector_store import VectorStore
from sarr.common.config import Settings, get_settings
from sarr.common.schemas import SearchHit, SearchRequest, SearchResponse

logger = logging.getLogger("sarr.api")


class SearchService:
    def __init__(
        self,
        settings: Settings | None = None,
        embedder: QueryEmbedder | None = None,
        vector_store: VectorStore | None = None,
        reranker: Reranker | None = None,
        *,
        warm: bool = True,
    ) -> None:
        self.settings = settings or get_settings()
        self.embedder = embedder or QueryEmbedder(self.settings.embedding_model)
        self.vector_store = vector_store or VectorStore(self.settings)
        self.reranker = reranker or Reranker(self.settings.reranker_model)
        if warm and embedder is None:
            # Load model at startup so the first user request isn't penalized.
            started = time.perf_counter()
            self.embedder.embed("warmup")
            logger.info(
                "embedder warmed device=%s took_ms=%.1f",
                getattr(self.embedder.model, "device", "unknown"),
                (time.perf_counter() - started) * 1000.0,
            )

    def search(self, request: SearchRequest) -> SearchResponse:
        rerank = (
            self.settings.rerank_enabled_default
            if request.rerank is None
            else request.rerank
        )
        timing_ms: dict[str, float] = {}

        t0 = time.perf_counter()
        query_vector = self.embedder.embed(request.query)
        timing_ms["embed_ms"] = round((time.perf_counter() - t0) * 1000.0, 1)

        t1 = time.perf_counter()
        raw_hits = self.vector_store.search(
            query_vector,
            limit=max(self.settings.search_top_k, request.limit),
            query_filter=self._build_filter(request),
        )
        timing_ms["qdrant_ms"] = round((time.perf_counter() - t1) * 1000.0, 1)

        candidates = raw_hits[: self.settings.rerank_top_k] if rerank else raw_hits
        relevance_scores = [hit["score"] for hit in candidates]

        if rerank and candidates:
            t2 = time.perf_counter()
            documents = [self._candidate_text(hit["payload"]) for hit in candidates]
            relevance_scores = self.reranker.rerank(request.query, documents)
            timing_ms["rerank_ms"] = round((time.perf_counter() - t2) * 1000.0, 1)
            # Min-max normalize reranker scores into 0..1 for blending
            lo, hi = min(relevance_scores), max(relevance_scores)
            if hi > lo:
                relevance_scores = [(s - lo) / (hi - lo) for s in relevance_scores]
            else:
                relevance_scores = [1.0 for _ in relevance_scores]
        else:
            timing_ms["rerank_ms"] = 0.0

        t3 = time.perf_counter()
        scored: list[tuple[float, dict[str, Any]]] = []
        for hit, rel in zip(candidates, relevance_scores, strict=True):
            final = blend_scores(
                rel,
                hit["payload"],
                alpha=self.settings.rank_alpha,
                beta=self.settings.rank_beta,
                delta=self.settings.rank_delta,
            )
            scored.append((final, hit))

        scored.sort(key=lambda item: item[0], reverse=True)
        results = [self._to_hit(score, hit) for score, hit in scored[: request.limit]]
        timing_ms["blend_ms"] = round((time.perf_counter() - t3) * 1000.0, 1)

        return SearchResponse(
            query=request.query,
            total=len(results),
            reranked=rerank,
            results=results,
            timing_ms=timing_ms,
        )

    def _build_filter(self, request: SearchRequest) -> dict[str, Any] | None:
        if not request.filters:
            return None
        must: list[dict[str, Any]] = []
        f = request.filters
        if f.min_stars is not None:
            must.append(
                {
                    "key": "stars",
                    "range": {"gte": f.min_stars},
                }
            )
        if f.license:
            must.append({"key": "license", "match": {"value": f.license}})
        if f.requires_python:
            must.append(
                {"key": "requires_python", "match": {"value": f.requires_python}}
            )
        return {"must": must} if must else None

    @staticmethod
    def _candidate_text(payload: dict[str, Any]) -> str:
        name = payload.get("name") or ""
        summary = payload.get("summary") or ""
        return f"{name}\n{summary}".strip()

    @staticmethod
    def _to_hit(score: float, hit: dict[str, Any]) -> SearchHit:
        payload = hit["payload"]
        return SearchHit(
            name=payload.get("name") or str(hit["id"]),
            summary=payload.get("summary"),
            score=score,
            stars=int(payload.get("stars") or 0),
            forks=int(payload.get("forks") or 0),
            last_commit=payload.get("last_commit"),
            repo_url=payload.get("repo_url"),
            pypi_url=payload.get("pypi_url"),
            metadata={
                k: v
                for k, v in payload.items()
                if k
                not in {
                    "name",
                    "summary",
                    "stars",
                    "forks",
                    "last_commit",
                    "repo_url",
                    "pypi_url",
                }
            },
        )
