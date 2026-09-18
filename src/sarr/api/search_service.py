"""Orchestrates query embed → Qdrant ANN → BigQuery metadata → rerank → score blend."""

from __future__ import annotations

import logging
import time
from typing import Any

from sarr.api.embedder import QueryEmbedder
from sarr.api.metadata_store import PackageMetadataStore
from sarr.api.ranking import blend_scores
from sarr.api.reranker import Reranker
from sarr.api.vector_store import VectorStore
from sarr.common.config import Settings, get_settings
from sarr.common.schemas import SearchFilters, SearchHit, SearchRequest, SearchResponse

logger = logging.getLogger("sarr.api")


class SearchService:
    def __init__(
        self,
        settings: Settings | None = None,
        embedder: QueryEmbedder | None = None,
        vector_store: VectorStore | None = None,
        reranker: Reranker | None = None,
        metadata_store: PackageMetadataStore | None = None,
        *,
        warm: bool = True,
    ) -> None:
        self.settings = settings or get_settings()
        self.embedder = embedder or QueryEmbedder(self.settings.embedding_model)
        self.vector_store = vector_store or VectorStore(self.settings)
        self.reranker = reranker or Reranker(self.settings.reranker_model)
        self.metadata_store = metadata_store or PackageMetadataStore(self.settings)
        if warm and embedder is None:
            started = time.perf_counter()
            self.embedder.embed("warmup")
            logger.info(
                "embedder warmed took_ms=%.1f",
                (time.perf_counter() - started) * 1000.0,
            )

    def search(
        self,
        request: SearchRequest,
        *,
        retrieve_k: int | None = None,
        rerank_k: int | None = None,
        output_limit: int | None = None,
    ) -> SearchResponse:
        rerank = (
            self.settings.rerank_enabled_default
            if request.rerank is None
            else request.rerank
        )
        timing_ms: dict[str, float] = {}
        fetch_k = retrieve_k if retrieve_k is not None else self.settings.search_top_k
        rerank_keep = rerank_k if rerank_k is not None else self.settings.rerank_top_k
        limit = output_limit if output_limit is not None else request.limit

        t0 = time.perf_counter()
        query_vector = self.embedder.embed(request.query)
        timing_ms["embed_ms"] = round((time.perf_counter() - t0) * 1000.0, 1)

        # 1) Qdrant: vector similarity only (no ranking metadata).
        ann_limit = max(fetch_k, rerank_keep if rerank else fetch_k, limit)
        ann_limit *= self.settings.search_metadata_over_fetch
        t1 = time.perf_counter()
        vector_hits = self.vector_store.search(
            query_vector,
            limit=ann_limit,
            query_filter=None
            if self.settings.qdrant_vectors_only
            else self._build_filter(request),
            vectors_only=self.settings.qdrant_vectors_only,
        )
        timing_ms["qdrant_ms"] = round((time.perf_counter() - t1) * 1000.0, 1)

        # 2) BigQuery: hydrate metadata for retrieved package names.
        t2 = time.perf_counter()
        with_metadata = self._attach_metadata(vector_hits)
        timing_ms["bq_ms"] = round((time.perf_counter() - t2) * 1000.0, 1)

        filtered = [
            hit
            for hit in with_metadata
            if self._passes_filters(hit["payload"], request.filters)
        ]
        retrieval_pool = filtered[:fetch_k]

        # 3) Rerank using BigQuery summaries (never Qdrant payloads).
        relevance_scores: list[float]
        rerank_pool = retrieval_pool[:rerank_keep] if rerank else retrieval_pool
        if rerank and rerank_pool:
            t3 = time.perf_counter()
            documents = [self._candidate_text(hit["payload"]) for hit in rerank_pool]
            try:
                relevance_scores = self.reranker.rerank(request.query, documents)
            except ImportError:
                logger.warning("rerank requested but torch is unavailable; using vector scores")
                rerank = False
                relevance_scores = [hit["score"] for hit in rerank_pool]
                timing_ms["rerank_ms"] = 0.0
            else:
                timing_ms["rerank_ms"] = round((time.perf_counter() - t3) * 1000.0, 1)
                lo, hi = min(relevance_scores), max(relevance_scores)
                if hi > lo:
                    relevance_scores = [(s - lo) / (hi - lo) for s in relevance_scores]
                else:
                    relevance_scores = [1.0 for _ in relevance_scores]
        else:
            timing_ms["rerank_ms"] = 0.0
            relevance_scores = [hit["score"] for hit in rerank_pool]

        # 4) Blend semantic + popularity/recency from BigQuery payloads.
        t4 = time.perf_counter()
        scored: list[tuple[float, dict[str, Any]]] = []
        for hit, rel in zip(rerank_pool, relevance_scores, strict=True):
            final = blend_scores(
                rel,
                hit["payload"],
                alpha=self.settings.rank_alpha,
                beta=self.settings.rank_beta,
                delta=self.settings.rank_delta,
            )
            scored.append((final, hit))

        scored.sort(key=lambda item: item[0], reverse=True)
        results = [self._to_hit(score, hit) for score, hit in scored[:limit]]
        timing_ms["blend_ms"] = round((time.perf_counter() - t4) * 1000.0, 1)

        return SearchResponse(
            query=request.query,
            total=len(results),
            reranked=rerank,
            results=results,
            timing_ms=timing_ms,
        )

    def _attach_metadata(self, hits: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if not hits:
            return []

        if not self.settings.qdrant_vectors_only:
            return self._ensure_payload_metadata(hits)

        names = [name for hit in hits if (name := self._hit_name(hit))]
        unique_names = list(dict.fromkeys(names))
        if not unique_names:
            return []

        try:
            payloads = self.metadata_store.fetch_by_names(unique_names)
        except Exception as exc:
            logger.exception("BigQuery metadata fetch failed")
            raise RuntimeError(
                f"BigQuery metadata hydrate failed: {exc!s}. "
                "Fix: activate .venv, pip install -e '.[api]', set GCP_PROJECT_ID, "
                "run 'gcloud auth application-default login', restart make run-api."
            ) from exc

        enriched: list[dict[str, Any]] = []
        for hit in hits:
            name = self._hit_name(hit)
            if not name:
                continue
            payload = payloads.get(name)
            if not payload:
                continue
            enriched.append(
                {
                    "id": hit["id"],
                    "score": hit["score"],
                    "payload": payload,
                }
            )
        return enriched

    def _ensure_payload_metadata(self, hits: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Legacy collections: use Qdrant payload when it already includes summary."""
        needs_bq = [hit for hit in hits if not (hit.get("payload") or {}).get("summary")]
        if not needs_bq:
            return hits

        names = [name for hit in needs_bq if (name := self._hit_name(hit))]
        if not names:
            return hits

        try:
            payloads = self.metadata_store.fetch_by_names(list(dict.fromkeys(names)))
        except Exception:
            logger.exception("BigQuery metadata fetch failed for legacy hits")
            return hits

        out: list[dict[str, Any]] = []
        for hit in hits:
            payload = dict(hit.get("payload") or {})
            name = self._hit_name(hit)
            if name and not payload.get("summary"):
                payload = payloads.get(name) or payload
            if not payload.get("summary") and not payload.get("name"):
                continue
            out.append({**hit, "payload": payload})
        return out

    @staticmethod
    def _hit_name(hit: dict[str, Any]) -> str | None:
        payload = hit.get("payload") or {}
        name = payload.get("name")
        if isinstance(name, str) and name.strip():
            return name.strip().lower().replace("_", "-")
        return None

    @staticmethod
    def _passes_filters(payload: dict[str, Any], filters: SearchFilters | None) -> bool:
        if not filters:
            return True
        if filters.min_stars is not None:
            if int(payload.get("stars") or 0) < filters.min_stars:
                return False
        if filters.license:
            license_value = str(payload.get("license") or "")
            if filters.license.lower() not in license_value.lower():
                return False
        if filters.requires_python:
            req = str(payload.get("requires_python") or "")
            if filters.requires_python not in req:
                return False
        return True

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
