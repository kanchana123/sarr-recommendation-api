"""Unit tests for watermark helpers."""

from pathlib import Path

import pytest

from sarr.etl.watermark import load_watermark, parse_watermark, save_watermark


@pytest.mark.unit
def test_parse_watermark_strips_whitespace() -> None:
    assert parse_watermark(" 2024-01-15 ") == "2024-01-15"


@pytest.mark.unit
def test_save_and_load_watermark(tmp_path: Path) -> None:
    path = tmp_path / "last_update_date.txt"
    save_watermark(path, "2025-06-01")
    assert load_watermark(path) == "2025-06-01"


@pytest.mark.unit
def test_load_watermark_default_when_missing(tmp_path: Path) -> None:
    path = tmp_path / "missing.txt"
    assert load_watermark(path, default="1970-01-01") == "1970-01-01"
