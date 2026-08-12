"""End-to-end ETL orchestration used by Colab / CLI."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import date, datetime
from typing import Any

from sarr.common.config import Settings, get_settings
from sarr.common.schemas import PackageRecord
from sarr.etl.embed import BatchEmbedder
from sarr.etl.extract import extract_packages
from sarr.etl.load import QdrantLoader
from sarr.etl.transform import transform_package
from sarr.etl.watermark import parse_watermark, save_watermark


def run_etl(
    *,
    last_update_date: str | None = None,
    batch_size: int = 64,
    watermark_path: str | None = "data/last_update_date.txt",
    settings: Settings | None = None,
    packages: Iterable[PackageRecord] | None = None,
    embedder: BatchEmbedder | None = None,
    loader: QdrantLoader | None = None,
    bq_client: Any | None = None,
) -> dict[str, Any]:
    """Extract → transform → embed → upsert. Returns run stats."""
    cfg = settings or get_settings()
    watermark = parse_watermark(last_update_date or cfg.last_update_date)
    embedder = embedder or BatchEmbedder(cfg.embedding_model)
    loader = loader or QdrantLoader(cfg)
    loader.ensure_collection()

    source = (
        packages
        if packages is not None
        else extract_packages(watermark, settings=cfg, client=bq_client)
    )

    processed = 0
    max_update: date | datetime | None = None
    batch_ids: list[str] = []
    batch_docs: list[str] = []
    batch_payloads: list[dict[str, Any]] = []

    def flush() -> None:
        nonlocal processed, batch_ids, batch_docs, batch_payloads
        if not batch_ids:
            return
        vectors = embedder.embed_documents(batch_docs, batch_size=batch_size)
        loader.upsert_batch(batch_ids, vectors, batch_payloads)
        processed += len(batch_ids)
        batch_ids, batch_docs, batch_payloads = [], [], []

    for package in source:
        doc, payload, _digest = transform_package(package)
        batch_ids.append(package.name)
        batch_docs.append(doc)
        batch_payloads.append(payload)
        if package.update_date is not None:
            if max_update is None or package.update_date > max_update:
                max_update = package.update_date
        if len(batch_ids) >= batch_size:
            flush()

    flush()

    if max_update is not None and watermark_path:
        save_watermark(watermark_path, max_update)

    return {
        "processed": processed,
        "watermark_before": watermark,
        "watermark_after": parse_watermark(max_update) if max_update else watermark,
    }
