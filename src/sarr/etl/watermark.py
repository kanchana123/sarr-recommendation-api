"""Watermark helpers for incremental BigQuery sync."""

from datetime import date, datetime
from pathlib import Path


def parse_watermark(value: str | date | datetime) -> str:
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return value.strip()


def load_watermark(path: str | Path, default: str = "1970-01-01") -> str:
    file_path = Path(path)
    if not file_path.exists():
        return default
    return parse_watermark(file_path.read_text(encoding="utf-8"))


def save_watermark(path: str | Path, value: str | date | datetime) -> None:
    file_path = Path(path)
    file_path.parent.mkdir(parents=True, exist_ok=True)
    file_path.write_text(parse_watermark(value) + "\n", encoding="utf-8")
