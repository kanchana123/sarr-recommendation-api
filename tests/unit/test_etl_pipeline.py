"""Unit tests for ETL transform + pipeline with fakes."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import pytest

from sarr.common.schemas import PackageRecord
from sarr.etl.pipeline import run_etl
from sarr.etl.transform import to_payload, transform_package


class FakeEmbedder:
    def embed_documents(self, documents: Sequence[str], batch_size: int = 64) -> list[list[float]]:
        return [[float(len(doc)), 0.0, 0.0] for doc in documents]


class FakeLoader:
    def __init__(self) -> None:
        self.batches: list[tuple[list[str], list[list[float]], list[dict[str, Any]]]] = []

    def ensure_collection(self) -> None:
        return None

    def upsert_batch(
        self,
        ids: Sequence[str],
        vectors: Sequence[Sequence[float]],
        payloads: Sequence[dict[str, Any]],
    ) -> None:
        self.batches.append((list(ids), [list(v) for v in vectors], list(payloads)))


@pytest.mark.unit
def test_to_payload_includes_ranking_fields(sample_package: PackageRecord) -> None:
    payload = to_payload(sample_package)
    assert payload["name"] == "requests"
    assert payload["stars"] == 52000
    assert payload["forks"] == 9400
    assert "content_hash" in payload


@pytest.mark.unit
def test_transform_package_returns_document_and_hash(sample_package: PackageRecord) -> None:
    doc, payload, digest = transform_package(sample_package)
    assert "Package: requests" in doc
    assert payload["content_hash"] == digest


@pytest.mark.unit
def test_run_etl_with_injected_fakes(sample_package: PackageRecord, tmp_path) -> None:
    loader = FakeLoader()
    watermark_path = tmp_path / "wm.txt"
    package = sample_package.model_copy(update={"update_date": "2024-05-01"})

    stats = run_etl(
        last_update_date="1970-01-01",
        batch_size=10,
        watermark_path=str(watermark_path),
        packages=[package],
        embedder=FakeEmbedder(),  # type: ignore[arg-type]
        loader=loader,  # type: ignore[arg-type]
    )

    assert stats["processed"] == 1
    assert len(loader.batches) == 1
    assert loader.batches[0][0] == ["requests"]
    assert watermark_path.read_text(encoding="utf-8").startswith("2024-05-01")


@pytest.mark.unit
def test_run_etl_checkpoints_watermark_each_batch(tmp_path) -> None:
    loader = FakeLoader()
    watermark_path = tmp_path / "wm.txt"
    packages = [
        PackageRecord(name="a", summary="a", update_date="2024-01-01", stars=1),
        PackageRecord(name="b", summary="b", update_date="2024-01-02", stars=2),
        PackageRecord(name="c", summary="c", update_date="2024-01-03", stars=3),
    ]

    stats = run_etl(
        last_update_date="1970-01-01",
        batch_size=2,
        watermark_path=str(watermark_path),
        packages=packages,
        embedder=FakeEmbedder(),  # type: ignore[arg-type]
        loader=loader,  # type: ignore[arg-type]
    )

    assert stats["processed"] == 3
    assert len(loader.batches) == 2
    assert watermark_path.read_text(encoding="utf-8").startswith("2024-01-03")


@pytest.mark.unit
def test_run_etl_skips_validation_for_injected_packages(tmp_path) -> None:
    stats = run_etl(
        last_update_date="1970-01-01",
        batch_size=10,
        watermark_path=str(tmp_path / "wm.txt"),
        packages=[PackageRecord(name="x", summary="hello world")],
        embedder=FakeEmbedder(),  # type: ignore[arg-type]
        loader=FakeLoader(),  # type: ignore[arg-type]
    )
    assert stats["processed"] == 1
