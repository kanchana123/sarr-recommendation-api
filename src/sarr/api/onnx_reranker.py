"""CPU ONNX cross-encoder — no PyTorch on the request path."""

from __future__ import annotations

import json
import logging
from pathlib import Path

import numpy as np

logger = logging.getLogger("sarr.api")


def logits_to_scores(logits: np.ndarray) -> np.ndarray:
    """Turn classifier logits into one score per pair (MiniLM is usually 1-logit)."""
    array = np.asarray(logits, dtype=np.float32)
    if array.ndim == 2 and array.shape[1] == 1:
        return array[:, 0]
    if array.ndim == 2 and array.shape[1] == 2:
        shifted = array - array.max(axis=1, keepdims=True)
        exp = np.exp(shifted)
        return exp[:, 1] / np.clip(exp.sum(axis=1), 1e-12, None)
    return array.reshape(-1)


class OnnxCrossEncoder:
    """Loads tokenizer.json + model.onnx for query–document scoring."""

    def __init__(self, model_dir: str | Path) -> None:
        self.model_dir = Path(model_dir)
        config_path = self.model_dir / "rerank_config.json"
        config = json.loads(config_path.read_text()) if config_path.is_file() else {}
        self.max_length = int(config.get("max_length", 512))

        logger.info("importing onnxruntime for reranker")
        import onnxruntime as ort
        from tokenizers import Tokenizer

        logger.info("loading onnx reranker from %s", self.model_dir)
        self._tokenizer = Tokenizer.from_file(str(self.model_dir / "tokenizer.json"))
        pad_id = self._tokenizer.token_to_id("[PAD]")
        if pad_id is None:
            pad_id = 0
        self._tokenizer.enable_padding(pad_id=pad_id, pad_token="[PAD]")
        self._tokenizer.enable_truncation(max_length=self.max_length, strategy="longest_first")
        self._session = ort.InferenceSession(
            str(self.model_dir / "model.onnx"),
            providers=["CPUExecutionProvider"],
        )
        self._input_names = {inp.name for inp in self._session.get_inputs()}

    def predict(self, pairs: list[tuple[str, str]]) -> list[float]:
        if not pairs:
            return []
        encodings = self._tokenizer.encode_batch(list(pairs))
        input_ids = np.asarray([enc.ids for enc in encodings], dtype=np.int64)
        attention_mask = np.asarray([enc.attention_mask for enc in encodings], dtype=np.int64)
        token_type_ids = np.asarray([enc.type_ids for enc in encodings], dtype=np.int64)

        feeds: dict[str, np.ndarray] = {}
        if "input_ids" in self._input_names:
            feeds["input_ids"] = input_ids
        if "attention_mask" in self._input_names:
            feeds["attention_mask"] = attention_mask
        if "token_type_ids" in self._input_names:
            feeds["token_type_ids"] = token_type_ids

        logits = self._session.run(None, feeds)[0]
        return [float(score) for score in logits_to_scores(logits)]
