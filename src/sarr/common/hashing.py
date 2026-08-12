"""Content hashing to skip re-embedding unchanged packages."""

import hashlib

from sarr.common.documents import build_search_document
from sarr.common.schemas import PackageRecord


def content_hash(package: PackageRecord) -> str:
    """Stable hash of the embeddable search document."""
    document = build_search_document(package)
    return hashlib.sha256(document.encode("utf-8")).hexdigest()
