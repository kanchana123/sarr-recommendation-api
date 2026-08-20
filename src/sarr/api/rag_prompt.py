"""Build the grounded-recommendation prompt from retrieved packages only."""

from __future__ import annotations

from sarr.common.schemas import SearchHit

SYSTEM_INSTRUCTIONS = """You recommend PyPI packages for a user query.

Use only the candidate packages listed in the context. Do not invent package
names, APIs, or features that are not supported by those descriptions.

Return JSON only, with exactly three items:
{
  "recommendations": [
    {
      "package": "exact candidate name",
      "reason": "1-2 sentences explaining fit for the query",
      "cited_snippet": "a short exact excerpt copied from that package's description"
    }
  ]
}

Each "package" value must match a candidate name exactly. Each cited_snippet
must be copied from that same package's description text.
"""


def context_block(hits: list[SearchHit]) -> str:
    # Only name + description go to the LLM; stars and URLs stay out of generation context.
    lines: list[str] = []
    for index, hit in enumerate(hits, start=1):
        description = (hit.summary or "").strip() or "(no description)"
        lines.append(f"{index}. name: {hit.name}")
        lines.append(f"   description: {description}")
    return "\n".join(lines)


def build_prompt(query: str, hits: list[SearchHit]) -> str:
    return (
        f"{SYSTEM_INSTRUCTIONS}\n"
        f"User query:\n{query.strip()}\n\n"
        f"Candidate packages (name and description only):\n"
        f"{context_block(hits)}\n"
    )
