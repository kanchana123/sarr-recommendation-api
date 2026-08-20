"""Load Vertex credentials for Lambda (Secrets Manager) or local ADC."""

from __future__ import annotations

import json
import logging
from functools import lru_cache
from typing import Any

from sarr.common.config import Settings

logger = logging.getLogger("sarr.api")

_CLOUD_PLATFORM = ("https://www.googleapis.com/auth/cloud-platform",)


def vertex_credentials(settings: Settings) -> Any | None:
    """Return google.oauth2 service-account credentials, or None to use ADC."""
    info = _service_account_info(settings)
    if info is None:
        return None
    from google.oauth2 import service_account

    return service_account.Credentials.from_service_account_info(
        info,
        scopes=_CLOUD_PLATFORM,
    )


def _service_account_info(settings: Settings) -> dict[str, Any] | None:
    raw = (settings.gcp_service_account_json or "").strip()
    if not raw:
        raw = _secret_string(settings.gcp_credentials_secret_arn)
    if not raw:
        return None
    data = json.loads(raw)
    if not isinstance(data, dict) or data.get("type") != "service_account":
        raise RuntimeError("GCP credentials JSON is not a service account key")
    return data


def _secret_string(secret_id: str) -> str:
    if not (secret_id or "").strip():
        return ""
    return _fetch_secret(secret_id.strip())


@lru_cache(maxsize=4)
def _fetch_secret(secret_id: str) -> str:
    import boto3
    from botocore.exceptions import ClientError

    client = boto3.client("secretsmanager")
    try:
        resp = client.get_secret_value(SecretId=secret_id)
    except ClientError as exc:
        logger.warning("gcp credentials secret unavailable secret_id=%s", secret_id)
        raise RuntimeError(
            "GCP service account secret was not found. "
            "Create Secrets Manager secret "
            f"{secret_id} with a Vertex-enabled service account JSON."
        ) from exc
    return resp.get("SecretString") or ""
