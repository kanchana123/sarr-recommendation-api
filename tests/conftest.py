"""Shared pytest fixtures."""

from __future__ import annotations

import pytest

from sarr.common.schemas import PackageRecord


@pytest.fixture
def sample_package() -> PackageRecord:
    return PackageRecord(
        name="requests",
        summary="Python HTTP for Humans.",
        keywords=["http", "requests", "client"],
        dependencies=["urllib3", "certifi", "charset-normalizer", "idna"],
        classifiers=["Topic :: Internet :: WWW/HTTP"],
        stars=52000,
        forks=9400,
        downloads_30d=100000000,
        license="Apache-2.0",
        requires_python=">=3.8",
        repo_url="https://github.com/psf/requests",
        pypi_url="https://pypi.org/project/requests/",
    )
