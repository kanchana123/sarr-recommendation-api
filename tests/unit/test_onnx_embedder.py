"""Unit tests for ONNX pooling (no onnxruntime required)."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from sarr.api.embedder import QueryEmbedder
from sarr.api.onnx_embedder import pool_and_normalize


@pytest.mark.unit
def test_cls_pooling_uses_first_token_and_l2_normalizes() -> None:
    hidden = np.asarray(
        [[[3.0, 4.0, 0.0], [1.0, 0.0, 0.0]]],
        dtype=np.float32,
    )
    mask = np.asarray([[1, 1]], dtype=np.int64)
    vector = pool_and_normalize(hidden, mask, "cls")
    assert vector.shape == (1, 3)
    np.testing.assert_allclose(vector[0], [0.6, 0.8, 0.0], atol=1e-6)
    np.testing.assert_allclose(np.linalg.norm(vector[0]), 1.0, atol=1e-6)


@pytest.mark.unit
def test_query_embedder_uses_onnx_when_graph_present(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "model.onnx").write_bytes(b"onnx")

    class FakeOnnx:
        def __init__(self, model_dir: Path) -> None:
            self.model_dir = model_dir

        def embed(self, text: str) -> list[float]:
            return [0.1, 0.2, 0.3]

    monkeypatch.setattr("sarr.api.onnx_embedder.OnnxQueryEmbedder", FakeOnnx)
    embedder = QueryEmbedder(model_name=str(tmp_path))
    assert embedder.embed("query") == [0.1, 0.2, 0.3]


@pytest.mark.unit
def test_logits_to_scores_squeezes_single_logit() -> None:
    from sarr.api.onnx_reranker import logits_to_scores

    scores = logits_to_scores(np.asarray([[1.5], [-0.5]], dtype=np.float32))
    np.testing.assert_allclose(scores, [1.5, -0.5], atol=1e-6)


@pytest.mark.unit
def test_reranker_uses_onnx_when_graph_present(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from sarr.api.reranker import Reranker

    (tmp_path / "model.onnx").write_bytes(b"onnx")

    class FakeOnnx:
        def __init__(self, model_dir: Path) -> None:
            self.model_dir = model_dir

        def predict(self, pairs: list[tuple[str, str]]) -> list[float]:
            return [float(len(doc)) for _, doc in pairs]

    monkeypatch.setattr("sarr.api.onnx_reranker.OnnxCrossEncoder", FakeOnnx)
    reranker = Reranker(model_name=str(tmp_path))
    assert reranker.rerank("q", ["ab", "abcd"]) == [2.0, 4.0]


@pytest.mark.unit
def test_mean_pooling_masks_padding() -> None:
    hidden = np.asarray(
        [[[2.0, 0.0], [0.0, 2.0], [9.0, 9.0]]],
        dtype=np.float32,
    )
    mask = np.asarray([[1, 1, 0]], dtype=np.int64)
    vector = pool_and_normalize(hidden, mask, "mean")
    # mean of first two tokens = [1, 1], then L2 -> [1/sqrt(2), 1/sqrt(2)]
    expected = np.asarray([1.0, 1.0], dtype=np.float32)
    expected = expected / np.linalg.norm(expected)
    np.testing.assert_allclose(vector[0], expected, atol=1e-6)
