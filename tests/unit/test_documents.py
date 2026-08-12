"""Unit tests for search-document construction."""

import pytest

from sarr.common.documents import build_search_document
from sarr.common.hashing import content_hash
from sarr.common.schemas import PackageRecord


@pytest.mark.unit
def test_build_search_document_orders_identity_first(sample_package: PackageRecord) -> None:
    doc = build_search_document(sample_package)
    assert doc.startswith("Package: requests")
    assert "Summary: Python HTTP for Humans." in doc
    assert "Keywords: http, requests, client" in doc
    assert "Dependencies: urllib3" in doc
    assert "52000" not in doc
    assert "stars" not in doc.lower()


@pytest.mark.unit
def test_build_search_document_truncates_dependencies() -> None:
    package = PackageRecord(
        name="demo",
        summary="demo package",
        dependencies=[f"dep-{i}" for i in range(30)],
    )
    doc = build_search_document(package)
    assert "dep-0" in doc
    assert "dep-14" in doc
    assert "dep-15" not in doc


@pytest.mark.unit
def test_content_hash_stable(sample_package: PackageRecord) -> None:
    assert content_hash(sample_package) == content_hash(sample_package)


@pytest.mark.unit
def test_content_hash_changes_when_summary_changes(sample_package: PackageRecord) -> None:
    other = sample_package.model_copy(update={"summary": "changed"})
    assert content_hash(sample_package) != content_hash(other)
