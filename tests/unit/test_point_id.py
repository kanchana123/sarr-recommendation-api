"""Unit tests for stable Qdrant point ids."""

from __future__ import annotations

import pytest

from sarr.common.point_id import package_point_id


@pytest.mark.unit
def test_package_point_id_is_stable_and_normalized() -> None:
    a = package_point_id("Requests")
    b = package_point_id("requests")
    c = package_point_id("requests")
    assert a == b == c
    assert package_point_id("my_pkg") == package_point_id("my-pkg")
