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

    # RAG: retrieve 50, rerank all 50, pass top 8 to Gemini, emit top 3 after validation.
    rag_retrieve_k: int = 50
    rag_rerank_k: int = 50
    rag_context_k: int = 8
    rag_llm_timeout_s: float = 20.0
    vertex_location: str = "us-central1"
    vertex_gemini_model: str = "gemini-2.5-flash-lite"
    vertex_gemini_fallback_model: str = "gemini-2.5-flash"
    # Lambda: Secrets Manager secret id/ARN of a GCP service account JSON.
    # Local: leave empty and use gcloud ADC, or set GCP_SERVICE_ACCOUNT_JSON.
    gcp_credentials_secret_arn: str = ""
    gcp_service_account_json: str = ""

    # Ranking blend weights
    rank_alpha: float = 0.75
    rank_beta: float = 0.15
    rank_delta: float = 0.10


@lru_cache
def get_settings() -> Settings:
    return Settings()
