"""GCP credential loading for Vertex on Lambda."""

from __future__ import annotations

import json
import sys
import types

import pytest

from sarr.api.gcp_auth import _fetch_secret, vertex_credentials
from sarr.common.config import Settings


@pytest.mark.unit
def test_vertex_credentials_none_when_unset() -> None:
    assert vertex_credentials(Settings(gcp_project_id="sarr-505305")) is None


@pytest.mark.unit
def test_vertex_credentials_from_json(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict = {}

    sa_mod = types.ModuleType("google.oauth2.service_account")

    class Credentials:
        @staticmethod
        def from_service_account_info(info, scopes=None):
            captured["email"] = info["client_email"]
            captured["scopes"] = scopes
            return object()

    sa_mod.Credentials = Credentials
    oauth2_mod = types.ModuleType("google.oauth2")
    oauth2_mod.service_account = sa_mod
    google_mod = types.ModuleType("google")
    google_mod.oauth2 = oauth2_mod
    monkeypatch.setitem(sys.modules, "google", google_mod)
    monkeypatch.setitem(sys.modules, "google.oauth2", oauth2_mod)
    monkeypatch.setitem(sys.modules, "google.oauth2.service_account", sa_mod)

    info = {
        "type": "service_account",
        "client_email": "sarr-lambda-vertex@sarr-505305.iam.gserviceaccount.com",
    }
    creds = vertex_credentials(Settings(gcp_service_account_json=json.dumps(info)))
    assert creds is not None
    assert captured["email"] == info["client_email"]


@pytest.mark.unit
def test_rejects_user_adc_json() -> None:
    with pytest.raises(RuntimeError, match="not a service account"):
        vertex_credentials(Settings(gcp_service_account_json='{"type":"authorized_user"}'))


@pytest.mark.unit
def test_fetch_secret_uses_secrets_manager(monkeypatch: pytest.MonkeyPatch) -> None:
    _fetch_secret.cache_clear()

    class FakeClient:
        def get_secret_value(self, SecretId: str):
            assert SecretId == "sarr-search/gcp-vertex"
            return {"SecretString": '{"type":"service_account"}'}

    boto3_mod = types.ModuleType("boto3")
    boto3_mod.client = lambda *_a, **_k: FakeClient()
    botocore_mod = types.ModuleType("botocore")
    exc_mod = types.ModuleType("botocore.exceptions")
    exc_mod.ClientError = type("ClientError", (Exception,), {})
    monkeypatch.setitem(sys.modules, "boto3", boto3_mod)
    monkeypatch.setitem(sys.modules, "botocore", botocore_mod)
    monkeypatch.setitem(sys.modules, "botocore.exceptions", exc_mod)
    assert _fetch_secret("sarr-search/gcp-vertex") == '{"type":"service_account"}'
    _fetch_secret.cache_clear()
