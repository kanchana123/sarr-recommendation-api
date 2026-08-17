"""Drop LLM recommendations that cite packages outside the retrieved set."""

from __future__ import annotations

import json
import re

from pydantic import ValidationError

from sarr.common.schemas import (
    GroundedRecommendation,
    LlmRecommendationList,
    RagLlmResult,
    SearchHit,
)

_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.IGNORECASE)


def normalize_package_name(name: str) -> str:
    return name.strip().lower().replace("_", "-")


def _strip_fences(raw: str) -> str:
    text = raw.strip()
    if text.startswith("```"):
        text = _FENCE.sub("", text).strip()
    return text


def parse_llm_json(raw: str) -> LlmRecommendationList:
    payload = json.loads(_strip_fences(raw))
    return LlmRecommendationList.model_validate(payload)


def validate_recommendations(raw: str, hits: list[SearchHit]) -> RagLlmResult:
    by_name = {normalize_package_name(hit.name): hit for hit in hits}
    dropped: list[str] = []

    try:
        parsed = parse_llm_json(raw)
    except (json.JSONDecodeError, ValidationError, TypeError, ValueError) as exc:
        return RagLlmResult(
            recommendations=[],
            dropped=[],
            parse_error=str(exc),
        )

    grounded: list[GroundedRecommendation] = []
    for rec in parsed.recommendations:
        key = normalize_package_name(rec.package)
        hit = by_name.get(key)
        if hit is None:
            dropped.append(rec.package)
            continue
        if len(grounded) >= 3:
            continue
        description = hit.summary or ""
        snippet = rec.cited_snippet.strip()
        grounded.append(
            GroundedRecommendation(
                package=hit.name,
                reason=rec.reason.strip(),
                cited_snippet=snippet,
                snippet_in_description=_snippet_in_text(snippet, description),
            )
        )

    return RagLlmResult(recommendations=grounded, dropped=dropped)


def _snippet_in_text(snippet: str, description: str) -> bool:
    if not snippet:
        return False
    return _fold(snippet) in _fold(description)


def _fold(value: str) -> str:
    return " ".join(value.lower().split())
