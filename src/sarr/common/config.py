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
    # Default extract = PyPI distribution_metadata (~800k+ projects, current).
    gcp_project_id: str = ""
    etl_extract_source: str = Field(
        default="pypi",
        description="BigQuery extract: 'pypi' (full corpus) or 'libraries_io' (legacy ~168k).",
    )
    bq_pypi_project: str = "bigquery-public-data"
    bq_pypi_dataset: str = "pypi"
    bq_libraries_project: str = "bigquery-public-data"
    bq_libraries_dataset: str = "libraries_io"
    bq_source_project: str = "bigquery-public-data"
    bq_dataset: str = "libraries_io"
    bq_table: str = "projects"
    last_update_date: str = Field(
        default="1970-01-01",
        description="Watermark for incremental ETL. Use epoch date for full load.",
    )
    etl_min_description_length: int = Field(
        default=25,
        description="Min chars in summary or description (raises quality, shrinks corpus).",
    )
    etl_min_long_description_length: int = Field(
        default=80,
        description="Long-text bypass for popularity OR-gate (keeps well-documented packages).",
    )
    etl_active_within_days: int | None = Field(
        default=2920,
        description="Only packages with a release within this many days (~8 years).",
    )
    etl_require_libraries_io: bool = Field(
        default=False,
        description="If true, only PyPI projects known to Libraries.io (~168k).",
    )
    etl_require_any_popularity: bool = Field(
        default=True,
        description="Require stars/forks/sourcerank/dependents/long-description OR gate.",
    )
    etl_min_stars: int = Field(
        default=1,
        description="Popularity OR-gate: minimum GitHub stars (Libraries.io join).",
    )
    etl_min_forks: int = Field(
        default=1,
        description="Popularity OR-gate: minimum GitHub forks (Libraries.io join).",
    )
    etl_min_sourcerank: int = Field(
        default=4,
        description="Popularity OR-gate: minimum Libraries.io SourceRank.",
    )
    etl_min_dependent_projects: int = Field(
        default=1,
        description="Popularity OR-gate: minimum Libraries.io dependent project count.",
    )
    etl_max_packages: int | None = Field(
        default=700_000,
        description="Hard cap on indexed packages by popularity/recency (Qdrant sizing).",
    )

    # Qdrant
    qdrant_url: str = "http://localhost:6333"
    qdrant_api_key: str | None = None
    qdrant_collection: str = "sarr_pypi"
    qdrant_vectors_only: bool = Field(
        default=True,
        description="If true, upsert only vectors plus minimal {name} payload in Qdrant.",
    )
    search_metadata_over_fetch: int = Field(
        default=3,
        ge=1,
        le=10,
        description="ANN fetch multiplier before BigQuery hydrate + payload filters.",
    )

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
