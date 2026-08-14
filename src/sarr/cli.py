"""SARR command-line interface."""

from __future__ import annotations

import argparse
import json
import os
import sys
import time

import httpx

from sarr.common.schemas import SearchRequest, SearchResponse

DEFAULT_API_URL = "http://localhost:8080"


def resolve_api_url(cli_value: str | None = None) -> str:
    """Resolve API base URL: --api-url > SARR_API_URL > localhost default."""
    if cli_value:
        return cli_value.rstrip("/")
    env_url = os.environ.get("SARR_API_URL", "").strip()
    if env_url:
        return env_url.rstrip("/")
    return DEFAULT_API_URL


def _print_results(response: SearchResponse, *, as_json: bool) -> None:
    if as_json:
        print(response.model_dump_json(indent=2))
        return

    took = f"{response.took_ms:.0f} ms" if response.took_ms is not None else "n/a"
    mode = "reranked" if response.reranked else "semantic"
    print(f"Query: {response.query!r}  ({response.total} hits, {mode}, {took})")
    print("-" * 72)
    for i, hit in enumerate(response.results, start=1):
        summary = (hit.summary or "").strip().replace("\n", " ")
        if len(summary) > 100:
            summary = summary[:97] + "..."
        print(f"{i:>2}. {hit.name}  score={hit.score:.3f}  ★{hit.stars}")
        if summary:
            print(f"    {summary}")
        if hit.pypi_url:
            print(f"    {hit.pypi_url}")


def _search_via_api(request: SearchRequest, api_url: str) -> SearchResponse:
    base = api_url.rstrip("/")
    started = time.perf_counter()
    with httpx.Client(timeout=120.0) as client:
        res = client.post(f"{base}/v1/search", json=request.model_dump(exclude_none=True))
        res.raise_for_status()
        payload = res.json()
    response = SearchResponse.model_validate(payload)
    if response.took_ms is None:
        response.took_ms = round((time.perf_counter() - started) * 1000.0, 1)
    return response


def _search_local(request: SearchRequest) -> SearchResponse:
    from sarr.api.search_service import SearchService

    started = time.perf_counter()
    response = SearchService().search(request)
    response.took_ms = round((time.perf_counter() - started) * 1000.0, 1)
    return response


def cmd_search(args: argparse.Namespace) -> int:
    request = SearchRequest(query=args.query, limit=args.limit, rerank=args.rerank)
    try:
        if args.local:
            response = _search_local(request)
        else:
            api_url = resolve_api_url(args.api_url)
            response = _search_via_api(request, api_url)
    except Exception as exc:  # noqa: BLE001
        hint = ""
        if not args.local:
            hint = (
                "\nIs the API running? Try: make run-api"
                "\nOr set SARR_API_URL / use --local for in-process search."
            )
        print(f"error: {exc}{hint}", file=sys.stderr)
        return 1

    _print_results(response, as_json=args.json)
    return 0


def cmd_health(args: argparse.Namespace) -> int:
    base = resolve_api_url(args.api_url)
    try:
        with httpx.Client(timeout=15.0) as client:
            res = client.get(f"{base}/healthz")
            res.raise_for_status()
            print(json.dumps(res.json(), indent=2))
    except Exception as exc:  # noqa: BLE001
        print(
            f"error: {exc}\nIs the API running at {base}?",
            file=sys.stderr,
        )
        return 1
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="sarr",
        description="SARR — semantic search for PyPI packages",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    search = sub.add_parser("search", help="Search packages by natural language")
    search.add_argument("query", help="Search query, e.g. 'async HTTP client'")
    search.add_argument("-n", "--limit", type=int, default=5, help="Number of results (default 5)")
    search.add_argument(
        "--rerank",
        action="store_true",
        help="Enable cross-encoder reranking",
    )
    search.add_argument(
        "--api-url",
        default=None,
        help="API base URL (default: $SARR_API_URL or http://localhost:8080)",
    )
    search.add_argument(
        "--local",
        action="store_true",
        help="Run search in-process instead of calling the API "
        "(needs pip install 'sarr[api]' and Qdrant env)",
    )
    search.add_argument("--json", action="store_true", help="Print JSON instead of a table")
    search.set_defaults(func=cmd_search)

    health = sub.add_parser("health", help="Check a running SARR API")
    health.add_argument(
        "--api-url",
        default=None,
        help="API base URL (default: $SARR_API_URL or http://localhost:8080)",
    )
    health.set_defaults(func=cmd_health)

    return parser


def main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)
    raise SystemExit(args.func(args))


if __name__ == "__main__":
    main()
