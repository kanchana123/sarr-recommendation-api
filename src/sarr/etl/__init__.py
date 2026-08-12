"""ETL package: BigQuery extract → transform → embed → Qdrant load.

Designed to be imported from the Colab notebook or run as a CLI locally.
"""

from sarr.etl.pipeline import run_etl

__all__ = ["run_etl"]
