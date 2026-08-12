"""Orchestrates query embed → ANN → optional rerank → score blend."""

from __future__ import annotations

from typing import Any

from sarr.api.embedder import QueryEmbedder
from sarr.api.ranking import blend_scores
from sarr.api.reranker import Reranker
from sarr.api.vector_store import VectorStore
from sarr.common.config import Settings, get_settings
from sarr.common.schemas import SearchHit, SearchRequest, SearchResponse


class SearchService:
    def __init__(
        self,
        settings: Settings | None = None,
        embedder: QueryEmbedder | None = None,
        vector_store: VectorStore | None = None,
        reranker: Reranker | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.embedder = embedder or QueryEmbedder(self.settings.embedding_model)
        self.vector_store = vector_store or VectorStore(self.settings)
        self.reranker = reranker or Reranker(self.settings.reranker_model)

    def search(self, request: SearchRequest) -> SearchResponse:
        rerank = (
            self.settings.rerank_enabled_default
            if request.rerank is None
            else request.rerank
        )
        query_vector = self.embedder.embed(request.query)
        raw_hits = self.vector_store.search(
            query_vector,
            limit=max(self.settings.search_top_k, request.limit),
            query_filter=self._build_filter(request),
        )

        candidates = raw_hits[: self.settings.rerank_top_k] if rerank else raw_hits
        relevance_scores = [hit["score"] for hit in candidates]

        if rerank and candidates:
            documents = [
                self._candidate_text(hit["payload"]) for hit in candidates
            ]
            relevance_scores = self.reranker.rerank(request.query, documents)
            # Min-max normalize reranker scores into 0..1 for blending
            lo, hi = min(relevance_scores), max(relevance_scores)
            if hi > lo:
                relevance_scores = [(s - lo) / (hi - lo) for s in relevance_scores]
            else:
                relevance_scores = [1.0 for _ in relevance_scores]

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
        results = [
            self._to_hit(score, hit) for score, hit in scored[: request.limit]
        ]
        return SearchResponse(
            query=request.query,
            total=len(results),
            reranked=rerank,
            results=results,
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
