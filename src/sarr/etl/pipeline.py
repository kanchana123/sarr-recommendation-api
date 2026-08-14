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
from sarr.etl.validate import validate_etl_settings
from sarr.etl.watermark import load_watermark, parse_watermark, save_watermark


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
    skip_config_validation: bool = False,
) -> dict[str, Any]:
    """Extract → transform → embed → upsert. Returns run stats.

    After each successful Qdrant batch, the watermark file is updated so a Colab
    disconnect can resume without redoing finished work. Qdrant upserts are
    idempotent (stable id per package name), so re-running overlap is safe.
    """
    cfg = settings or get_settings()
    if packages is None and not skip_config_validation:
        problems = validate_etl_settings(cfg)
        if problems:
            raise ValueError(
                "ETL config invalid:\n- " + "\n- ".join(problems)
            )

    if last_update_date is not None:
        watermark = parse_watermark(last_update_date)
    elif watermark_path:
        watermark = load_watermark(watermark_path, default=cfg.last_update_date)
    else:
        watermark = parse_watermark(cfg.last_update_date)

    embedder = embedder or BatchEmbedder(cfg.embedding_model)
    loader = loader or QdrantLoader(cfg)
    loader.ensure_collection()

    source = (
        packages
        if packages is not None
        else extract_packages(watermark, settings=cfg, client=bq_client)
    )

    processed = 0
    max_update: date | datetime | str | None = None
    batch_ids: list[str] = []
    batch_docs: list[str] = []
    batch_payloads: list[dict[str, Any]] = []
    batch_updates: list[date | datetime | str] = []

    def flush() -> None:
        nonlocal processed, batch_ids, batch_docs, batch_payloads, batch_updates, max_update
        if not batch_ids:
            return
        print(f"[etl] upserting batch of {len(batch_ids)} (processed so far={processed})")
        vectors = embedder.embed_documents(batch_docs, batch_size=batch_size)
        loader.upsert_batch(batch_ids, vectors, batch_payloads)
        processed += len(batch_ids)

        if batch_updates:
            batch_max = max(batch_updates)
            if max_update is None or batch_max > max_update:
                max_update = batch_max
            # Checkpoint after successful upsert so Colab can resume.
            if watermark_path:
                save_watermark(watermark_path, max_update)

        batch_ids, batch_docs, batch_payloads, batch_updates = [], [], [], []

    for package in source:
        doc, payload, _digest = transform_package(package)
        batch_ids.append(package.name)
        batch_docs.append(doc)
        batch_payloads.append(payload)
        if package.update_date is not None:
            batch_updates.append(package.update_date)
        if len(batch_ids) >= batch_size:
            flush()

    flush()

    if processed == 0:
        print(
            "[etl] WARNING: processed=0 — nothing was uploaded to Qdrant. "
            "Check watermark, BigQuery filters, and that smoke-test returns rows."
        )

    return {
        "processed": processed,
        "watermark_before": watermark,
        "watermark_after": parse_watermark(max_update)
        if max_update is not None
        else watermark,
    }
