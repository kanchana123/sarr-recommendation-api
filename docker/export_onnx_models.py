"""Export bi-encoder and cross-encoder to ONNX during the Lambda image build."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import torch
from sentence_transformers import CrossEncoder, SentenceTransformer


def _torch_onnx_export(
    wrapped: torch.nn.Module,
    dummy: tuple[torch.Tensor, ...],
    onnx_path: Path,
    *,
    output_names: list[str],
    extra_dynamic: dict[str, dict[int, str]] | None = None,
) -> None:
    dynamic_axes = {
        "input_ids": {0: "batch", 1: "seq"},
        "attention_mask": {0: "batch", 1: "seq"},
        "token_type_ids": {0: "batch", 1: "seq"},
    }
    if extra_dynamic:
        dynamic_axes.update(extra_dynamic)
    export_kwargs = dict(
        model=wrapped,
        args=dummy,
        f=str(onnx_path),
        input_names=["input_ids", "attention_mask", "token_type_ids"],
        output_names=output_names,
        dynamic_axes=dynamic_axes,
        opset_version=17,
        do_constant_folding=True,
    )
    try:
        torch.onnx.export(**export_kwargs, dynamo=False)
    except TypeError:
        torch.onnx.export(**export_kwargs)


def _export_biencoder(model: SentenceTransformer, out_dir: Path) -> None:
    transformer = model[0].auto_model.eval()
    if hasattr(transformer, "config"):
        transformer.config._attn_implementation = "eager"
    tokenizer = model.tokenizer
    max_length = int(getattr(model, "max_seq_length", 512) or 512)

    pooling = "cls"
    try:
        pooling_config = model[1].get_config_dict()
        if pooling_config.get("pooling_mode_mean_tokens"):
            pooling = "mean"
        elif pooling_config.get("pooling_mode_cls_token"):
            pooling = "cls"
    except Exception:  # noqa: BLE001 — default matches BGE
        pooling = "cls"

    class _Wrapper(torch.nn.Module):
        def __init__(self, inner: torch.nn.Module) -> None:
            super().__init__()
            self.inner = inner

        def forward(self, input_ids, attention_mask, token_type_ids):
            out = self.inner(
                input_ids=input_ids,
                attention_mask=attention_mask,
                token_type_ids=token_type_ids,
            )
            return out.last_hidden_state

    wrapped = _Wrapper(transformer).eval()
    dummy_len = 16
    dummy = (
        torch.ones(1, dummy_len, dtype=torch.long),
        torch.ones(1, dummy_len, dtype=torch.long),
        torch.zeros(1, dummy_len, dtype=torch.long),
    )
    _torch_onnx_export(
        wrapped,
        dummy,
        out_dir / "model.onnx",
        output_names=["last_hidden_state"],
        extra_dynamic={"last_hidden_state": {0: "batch", 1: "seq"}},
    )
    tokenizer.save_pretrained(out_dir)
    (out_dir / "embed_config.json").write_text(
        json.dumps(
            {"pooling": pooling, "normalize": True, "max_length": max_length, "dim": 384},
            indent=2,
        )
        + "\n"
    )


def _onnx_vector(out_dir: Path, text: str) -> np.ndarray:
    import onnxruntime as ort
    from tokenizers import Tokenizer

    config = json.loads((out_dir / "embed_config.json").read_text())
    tokenizer = Tokenizer.from_file(str(out_dir / "tokenizer.json"))
    tokenizer.enable_truncation(max_length=int(config["max_length"]))
    encoded = tokenizer.encode(text)
    input_ids = np.asarray([encoded.ids], dtype=np.int64)
    attention_mask = np.asarray([encoded.attention_mask], dtype=np.int64)
    token_type_ids = np.asarray([encoded.type_ids], dtype=np.int64)
    session = ort.InferenceSession(str(out_dir / "model.onnx"), providers=["CPUExecutionProvider"])
    hidden = session.run(
        None,
        {
            "input_ids": input_ids,
            "attention_mask": attention_mask,
            "token_type_ids": token_type_ids,
        },
    )[0]
    if config["pooling"] == "mean":
        mask = attention_mask.astype(np.float32)[:, :, None]
        pooled = (hidden * mask).sum(axis=1) / np.clip(mask.sum(axis=1), 1e-9, None)
    else:
        pooled = hidden[:, 0, :]
    pooled = pooled / np.clip(np.linalg.norm(pooled, axis=1, keepdims=True), 1e-12, None)
    return pooled[0]


def _export_cross_encoder(ce: CrossEncoder, out_dir: Path) -> None:
    inner = ce.model.eval()
    if hasattr(inner, "config"):
        inner.config._attn_implementation = "eager"
    tokenizer = ce.tokenizer
    max_length = int(getattr(ce, "max_length", None) or 512)

    class _Wrapper(torch.nn.Module):
        def __init__(self, model: torch.nn.Module) -> None:
            super().__init__()
            self.inner = model

        def forward(self, input_ids, attention_mask, token_type_ids):
            out = self.inner(
                input_ids=input_ids,
                attention_mask=attention_mask,
                token_type_ids=token_type_ids,
            )
            return out.logits

    wrapped = _Wrapper(inner).eval()
    dummy_len = 16
    dummy = (
        torch.ones(2, dummy_len, dtype=torch.long),
        torch.ones(2, dummy_len, dtype=torch.long),
        torch.zeros(2, dummy_len, dtype=torch.long),
    )
    _torch_onnx_export(
        wrapped,
        dummy,
        out_dir / "model.onnx",
        output_names=["logits"],
        extra_dynamic={"logits": {0: "batch"}},
    )
    tokenizer.save_pretrained(out_dir)
    (out_dir / "rerank_config.json").write_text(
        json.dumps({"max_length": max_length, "task": "cross-encoder"}, indent=2) + "\n"
    )


def _onnx_ce_scores(out_dir: Path, pairs: list[tuple[str, str]]) -> np.ndarray:
    import onnxruntime as ort
    from tokenizers import Tokenizer

    config = json.loads((out_dir / "rerank_config.json").read_text())
    tokenizer = Tokenizer.from_file(str(out_dir / "tokenizer.json"))
    pad_id = tokenizer.token_to_id("[PAD]")
    if pad_id is None:
        pad_id = 0
    tokenizer.enable_padding(pad_id=pad_id, pad_token="[PAD]")
    tokenizer.enable_truncation(max_length=int(config["max_length"]), strategy="longest_first")
    encodings = tokenizer.encode_batch(list(pairs))
    feeds = {
        "input_ids": np.asarray([enc.ids for enc in encodings], dtype=np.int64),
        "attention_mask": np.asarray([enc.attention_mask for enc in encodings], dtype=np.int64),
        "token_type_ids": np.asarray([enc.type_ids for enc in encodings], dtype=np.int64),
    }
    session = ort.InferenceSession(str(out_dir / "model.onnx"), providers=["CPUExecutionProvider"])
    logits = np.asarray(session.run(None, feeds)[0], dtype=np.float32)
    if logits.ndim == 2 and logits.shape[1] == 1:
        return logits[:, 0]
    if logits.ndim == 2 and logits.shape[1] == 2:
        shifted = logits - logits.max(axis=1, keepdims=True)
        exp = np.exp(shifted)
        return exp[:, 1] / np.clip(exp.sum(axis=1), 1e-12, None)
    return logits.reshape(-1)


def main() -> int:
    root = Path(sys.argv[1] if len(sys.argv) > 1 else "/opt/models")
    embed_dir = root / "bge-small-en-v1.5"
    rerank_dir = root / "ms-marco-MiniLM-L-6-v2"
    embed_dir.mkdir(parents=True, exist_ok=True)
    rerank_dir.mkdir(parents=True, exist_ok=True)

    model_id = "BAAI/bge-small-en-v1.5"
    print(f"loading {model_id}", flush=True)
    model = SentenceTransformer(model_id)
    print("exporting bi-encoder onnx", flush=True)
    _export_biencoder(model, embed_dir)
    probe = "async HTTP client"
    st_vec = np.asarray(model.encode(probe, normalize_embeddings=True), dtype=np.float32)
    onnx_vec = np.asarray(_onnx_vector(embed_dir, probe), dtype=np.float32)
    cosine = float(np.dot(st_vec, onnx_vec) / (np.linalg.norm(st_vec) * np.linalg.norm(onnx_vec)))
    print(f"st vs onnx cosine={cosine:.6f}", flush=True)
    if cosine < 0.999:
        print("ERROR: ONNX embedder does not match SentenceTransformer", flush=True)
        return 1
    print(f"wrote {embed_dir}", flush=True)

    ce_id = "cross-encoder/ms-marco-MiniLM-L-6-v2"
    print(f"loading {ce_id}", flush=True)
    ce = CrossEncoder(ce_id)
    print("exporting cross-encoder onnx", flush=True)
    _export_cross_encoder(ce, rerank_dir)
    pairs = [
        ("async HTTP client", "httpx is a next generation HTTP client for Python"),
        ("async HTTP client", "pandas is a dataframe library for tabular data"),
        ("async HTTP client", "aiohttp async HTTP client/server for asyncio"),
    ]
    st_scores = np.asarray(ce.predict(pairs), dtype=np.float32)
    onnx_scores = np.asarray(_onnx_ce_scores(rerank_dir, pairs), dtype=np.float32)
    corr = float(np.corrcoef(st_scores, onnx_scores)[0, 1])
    mae = float(np.max(np.abs(st_scores - onnx_scores)))
    print(
        f"ce vs onnx scores st={st_scores} onnx={onnx_scores} corr={corr:.6f} mae={mae:.4f}",
        flush=True,
    )
    same_order = np.array_equal(np.argsort(-st_scores), np.argsort(-onnx_scores))
    if corr < 0.99 or not same_order:
        print("ERROR: ONNX reranker does not match CrossEncoder", flush=True)
        return 1
    print(f"wrote {rerank_dir}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
