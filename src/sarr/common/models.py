"""Model identifiers shared by Colab ETL and Lambda query path."""

from dataclasses import dataclass

from sarr.common.config import Settings, get_settings


@dataclass(frozen=True)
class ModelConfig:
    embedding_model: str
    reranker_model: str
    embedding_dim: int

    @classmethod
    def from_settings(cls, settings: Settings | None = None) -> "ModelConfig":
        cfg = settings or get_settings()
        return cls(
            embedding_model=cfg.embedding_model,
            reranker_model=cfg.reranker_model,
            embedding_dim=cfg.embedding_dim,
        )
