#!/usr/bin/env python3
"""Measure SARR search latency (warmup, then sequential mixed queries).

Reports client round-trip and the server's took_ms / timing_ms breakdown.

  python scripts/measure_latency.py
  python scripts/measure_latency.py --url https://isz2aki1n2.execute-api.us-east-1.amazonaws.com
  SARR_API_URL=http://localhost:8080 python scripts/measure_latency.py --n 50
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import time
import urllib.error
import urllib.request

DEFAULT_QUERIES = [
    "async HTTP client",
    "machine learning",
    "text to speech",
    "dataframe library",
    "http client",
    "retries backoff",
    "plotting charts",
    "password hashing",
    "web scraping",
    "task queue",
]


def percentile(values: list[float], p: float) -> float:
    if not values:
        return float("nan")
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    k = (len(ordered) - 1) * (p / 100.0)
    lo = int(k)
    hi = min(lo + 1, len(ordered) - 1)
    frac = k - lo
    return ordered[lo] * (1 - frac) + ordered[hi] * frac


def search(url: str, query: str, *, rerank: bool, timeout: float) -> dict:
    payload = json.dumps({"query": query, "limit": 10, "rerank": rerank}).encode()
    request = urllib.request.Request(
        f"{url.rstrip('/')}/v1/search",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    started = time.perf_counter()
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = json.loads(response.read().decode())
            status = response.status
    except urllib.error.HTTPError as exc:
        body = {"error": exc.read().decode()[:300]}
        status = exc.code
    rtt_ms = (time.perf_counter() - started) * 1000.0
    timing = body.get("timing_ms") or {}
    return {
        "query": query,
        "ok": status == 200 and "results" in body,
        "status": status,
        "rtt_ms": round(rtt_ms, 1),
        "took_ms": body.get("took_ms"),
        "embed_ms": timing.get("embed_ms"),
        "qdrant_ms": timing.get("qdrant_ms"),
        "rerank_ms": timing.get("rerank_ms"),
        "reranked": body.get("reranked"),
        "error": body.get("error") or body.get("message"),
    }


def summarize(label: str, rows: list[dict], field: str) -> str:
    values = [float(row[field]) for row in rows if row.get("ok") and row.get(field) is not None]
    if not values:
        return f"{label:16}  n=0"
    return (
        f"{label:16}  n={len(values):<3}  "
        f"p50={percentile(values, 50):7.1f}  "
        f"p95={percentile(values, 95):7.1f}  "
        f"p99={percentile(values, 99):7.1f}  "
        f"max={max(values):7.1f}  ms"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--url",
        default=os.environ.get("SARR_API_URL", "http://localhost:8080"),
        help="API base URL (or $SARR_API_URL)",
    )
    parser.add_argument("--n", type=int, default=50, help="Warm sequential requests after warmup")
    parser.add_argument("--rerank", action="store_true", help="Send rerank=true")
    parser.add_argument("--timeout", type=float, default=90.0)
    parser.add_argument("--skip-warmup", action="store_true")
    args = parser.parse_args()

    print(f"API {args.url}  n={args.n}  rerank={args.rerank}", flush=True)

    if not args.skip_warmup:
        print("warmup…", flush=True)
        warm = search(args.url, "warmup query", rerank=args.rerank, timeout=args.timeout)
        print(
            f"  warmup rtt={warm['rtt_ms']} ms  took_ms={warm['took_ms']}  "
            f"ok={warm['ok']}  status={warm['status']}",
            flush=True,
        )
        if not warm["ok"]:
            print(f"  warmup failed: {warm.get('error')}", file=sys.stderr)
            return 1

    rows: list[dict] = []
    for i in range(args.n):
        query = DEFAULT_QUERIES[i % len(DEFAULT_QUERIES)]
        row = search(args.url, query, rerank=args.rerank, timeout=args.timeout)
        rows.append(row)
        mark = "ok" if row["ok"] else f"FAIL {row['status']}"
        print(
            f"  {i + 1:>3}/{args.n} {mark:8}  rtt={row['rtt_ms']:7.1f}  "
            f"took={row['took_ms']}  embed={row['embed_ms']}  "
            f"qdrant={row['qdrant_ms']}  {query!r}",
            flush=True,
        )

    ok_rows = [row for row in rows if row["ok"]]
    print()
    print(summarize("client RTT", ok_rows, "rtt_ms"))
    print(summarize("server took_ms", ok_rows, "took_ms"))
    print(summarize("embed_ms", ok_rows, "embed_ms"))
    print(summarize("qdrant_ms", ok_rows, "qdrant_ms"))
    print(f"success {len(ok_rows)}/{len(rows)}")
    return 0 if len(ok_rows) == len(rows) else 2


if __name__ == "__main__":
    raise SystemExit(main())
