"""Stable Qdrant point ids for PyPI package names."""

from __future__ import annotations

import uuid


def package_point_id(package_name: str) -> str:
    normalized = package_name.strip().lower().replace("_", "-")
    return str(uuid.uuid5(uuid.NAMESPACE_DNS, f"pypi:{normalized}"))
