"""CLI entry for local ETL runs (Colab should call run_etl() directly)."""

from __future__ import annotations

import argparse
import json

from sarr.etl.pipeline import run_etl


def main() -> None:
    parser = argparse.ArgumentParser(description="SARR BigQuery → Qdrant ETL")
    parser.add_argument(
        "--last-update-date",
        default=None,
        help="Watermark (ISO date). Defaults to LAST_UPDATE_DATE env / 1970-01-01.",
    )
    parser.add_argument("--batch-size", type=int, default=64)
    parser.add_argument(
        "--watermark-path",
        default="data/last_update_date.txt",
        help="Where to persist the advanced watermark after a successful run.",
    )
    args = parser.parse_args()
    stats = run_etl(
        last_update_date=args.last_update_date,
        batch_size=args.batch_size,
        watermark_path=args.watermark_path,
    )
    print(json.dumps(stats, indent=2))


if __name__ == "__main__":
    main()
