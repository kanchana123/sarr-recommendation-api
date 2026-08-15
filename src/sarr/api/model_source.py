"""Resolve baked-in model directories for Lambda's read-only /var/task."""

from __future__ import annotations

import logging
import os
import shutil
from pathlib import Path

logger = logging.getLogger("sarr.api")


def resolve_model_source(model_name: str) -> str:
    """Return a writable model path when the image path is read-only.

    Hugging Face / sentence-transformers may lock or write next to weights.
    Lambda mounts ``/var/task`` read-only, which can hang a cold start until
    the function times out. Copy once into ``/tmp`` (writable) when needed.
    """
    src = Path(model_name)
    if not src.is_dir():
        return model_name
    if os.access(src, os.W_OK):
        return model_name

    dst = Path(os.environ.get("TMPDIR", "/tmp")) / "sarr-models" / src.name
    if not dst.exists():
        logger.info("copying model %s -> %s", src, dst)
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(src, dst)
    return str(dst)
