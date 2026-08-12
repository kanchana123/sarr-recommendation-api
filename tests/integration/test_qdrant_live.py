"""Integration placeholders — require Docker / live Qdrant.

Mark and skip by default so CI stays fast without secrets.
"""

import os

import pytest

pytestmark = pytest.mark.integration


@pytest.mark.skipif(
    os.getenv("SARR_RUN_INTEGRATION") != "1",
    reason="Set SARR_RUN_INTEGRATION=1 to run live integration tests",
)
def test_qdrant_connectivity_placeholder() -> None:
    from qdrant_client import QdrantClient

    url = os.environ["QDRANT_URL"]
    api_key = os.getenv("QDRANT_API_KEY")
    client = QdrantClient(url=url, api_key=api_key)
    collections = client.get_collections()
    assert collections is not None
