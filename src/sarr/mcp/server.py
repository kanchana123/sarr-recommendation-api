"""MCP stdio server — tool wrapper around the SARR HTTP API."""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from sarr.mcp import api_client

mcp = FastMCP(
    "SARR",
    instructions=(
        "Semantic search and grounded recommendations for PyPI packages. "
        "Use search_packages for fast lookup. Use recommend_packages only when "
        "you need a citation-checked top-3 from retrieved descriptions. "
        "Never invent package names — only use names returned by these tools."
    ),
)


@mcp.tool(name="search_packages")
def search_packages_tool(
    query: str,
    limit: int = 10,
    rerank: bool = False,
) -> dict:
    """Search PyPI packages by natural language (POST /v1/search).

    Args:
        query: What the user is looking for, e.g. 'async HTTP client with retries'.
        limit: Max hits to return (1–50).
        rerank: If true, rescore with a cross-encoder (slower, often better order).
    """
    return api_client.search_packages(query, limit=limit, rerank=rerank)


@mcp.tool(name="recommend_packages")
def recommend_packages_tool(
    query: str,
    rerank: bool = True,
    include_ranked_list: bool = True,
) -> dict:
    """Grounded top-3 recommendations via RAG (POST /v1/rag, SSE collapsed to JSON).

    Waits for ranked_list then llm_done (or llm_error). If generation fails, ranked
    hits are still returned and error is set — use ranked when recommendations is empty.

    Args:
        query: Natural language need, e.g. 'speech to text library'.
        rerank: Cross-encoder rerank before prompting Gemini (default true).
        include_ranked_list: Include the retrieved ranked hits (default true).
    """
    return api_client.recommend_packages(
        query,
        rerank=rerank,
        include_ranked_list=include_ranked_list,
    )


@mcp.tool(name="health")
def health_tool() -> dict:
    """Check whether the SARR API is reachable (GET /healthz)."""
    return api_client.health_check()


def main() -> None:
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
