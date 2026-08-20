"""Vertex AI Gemini streaming client for grounded RAG recommendations."""

from __future__ import annotations

import logging
from collections.abc import Iterator
from typing import Protocol

from sarr.common.config import Settings, get_settings

logger = logging.getLogger("sarr.api")


class TokenStreamer(Protocol):
    def stream(self, prompt: str) -> Iterator[str]: ...


class VertexGeminiStreamer:
    """Streams tokens from Gemini on Vertex AI. No OpenAI dependency."""

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self._client = None

    def configured(self) -> bool:
        # Lets RagService skip Gemini when GCP_PROJECT_ID is unset (ranked_list still works).
        return bool(self.settings.gcp_project_id)

    def stream(self, prompt: str) -> Iterator[str]:
        if not self.configured():
            raise RuntimeError(
                "GCP_PROJECT_ID is not set; Vertex AI Gemini cannot run."
            )
        client = self._get_client()
        models = [
            self.settings.vertex_gemini_model,
            self.settings.vertex_gemini_fallback_model,
        ]
        last_error: Exception | None = None
        for model in models:
            yielded = False
            try:
                for token in self._stream_model(client, model, prompt):
                    yielded = True
                    yield token
                return
            except Exception as exc:  # noqa: BLE001 — try fallback before any tokens
                last_error = exc
                logger.warning("gemini stream failed model=%s error=%s", model, exc)
                # Do not switch models mid-stream; partial JSON cannot be validated.
                if yielded:
                    raise RuntimeError(f"Gemini streaming failed: {exc}") from exc
        raise RuntimeError(f"Gemini streaming failed: {last_error}") from last_error

    def _get_client(self):
        if self._client is None:
            from google import genai

            self._client = genai.Client(
                vertexai=True,
                project=self.settings.gcp_project_id,
                location=self.settings.vertex_location,
            )
        return self._client

    def _stream_model(self, client, model: str, prompt: str) -> Iterator[str]:
        from google.genai import types

        logger.info("gemini stream start model=%s", model)
        stream = client.models.generate_content_stream(
            model=model,
            contents=prompt,
            config=types.GenerateContentConfig(
                temperature=0.2,
                # Ask Vertex for JSON so validate_recommendations can parse a fixed shape.
                response_mime_type="application/json",
            ),
        )
        for chunk in stream:
            text = getattr(chunk, "text", None)
            if text:
                yield text
