"""Application settings loaded from environment variables."""

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # BigQuery / ETL
    # gcp_project_id = YOUR project (billing/quota for the query job).
    # Default source = Libraries.io public dataset (PyPI + repo stars).
    gcp_project_id: str = ""
    bq_source_project: str = "bigquery-public-data"
    bq_dataset: str = "libraries_io"
    bq_table: str = "projects"
    last_update_date: str = Field(
        default="1970-01-01",
        description="Watermark for incremental ETL. Use epoch date for full load.",
    )

    # Qdrant
    qdrant_url: str = "http://localhost:6333"
    qdrant_api_key: str | None = None
    qdrant_collection: str = "sarr_pypi"

    # Models (must match Colab ETL and Lambda)
    embedding_model: str = "BAAI/bge-small-en-v1.5"
    reranker_model: str = "cross-encoder/ms-marco-MiniLM-L-6-v2"
    embedding_dim: int = 384

    # Search
    rerank_enabled_default: bool = False
    search_top_k: int = 50
    rerank_top_k: int = 20

    # Ranking blend weights
    rank_alpha: float = 0.75
    rank_beta: float = 0.15
    rank_delta: float = 0.10


@lru_cache
def get_settings() -> Settings:
    return Settings()
