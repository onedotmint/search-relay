#!/usr/bin/env python3
"""Concurrency + large-payload benchmark for Search Relay.

Runs two ladders against a LOCAL mock upstream (no real provider keys):

1. Concurrency: 1 / 10 / 50 / 100 concurrent relay requests -> p50/p95/p99,
   requests/sec, and failure count (including any SQLite contention surfacing
   as non-200 responses).
2. Payload: POST bodies of 100 KB / 1 MB / 5 MB / 10 MB / 20 MB -> duration
   and peak RSS.

Verdict rule: if the concurrency ladder completes without failures, SQLite is
fine for this workload — do NOT change the DB architecture. Only if you see
"database is locked"-class failures should you investigate WAL mode,
busy_timeout, or connection management.

Usage:
    python scripts/benchmark.py [--concurrency 1,10,50,100] [--requests 100]
"""

import argparse
import asyncio
import json
import os
import resource
import statistics
import sys
import tempfile
import threading
import time
from contextlib import ExitStack
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import httpx
import uvicorn

# Make the project importable when the script runs from anywhere.
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# Patch the registry BEFORE importing app.main so the exa search route points
# at the local mock. The relay reads the registry at request time.
import app.providers as providers  # noqa: E402
from app.db import connect, init_db
from app.repositories import (
    create_provider,
    create_provider_api_key,
    create_relay_key,
)
from app.security import fingerprint_secret, hash_secret

MOCK_QUERY = {"query": "benchmark", "numResults": 3}


class MockHandler(BaseHTTPRequestHandler):
    def do_POST(self):  # noqa: N802 (http.server API)
        length = int(self.headers.get("Content-Length", 0))
        self.rfile.read(length)
        time.sleep(0.01)  # simulate a plausible upstream latency
        body = json.dumps({"results": [{"title": f"result-{i}", "url": f"https://example.com/{i}"} for i in range(3)]}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):  # silence request logs
        pass


def start_mock_upstream() -> tuple[ThreadingHTTPServer, str]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), MockHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    return server, f"http://{host}:{port}/search"


def patch_registry(mock_url: str) -> None:
    exa = providers.PROVIDERS["exa"]
    search_route = exa.routes["search"]
    providers.PROVIDERS["exa"] = replace(
        exa,
        routes={**exa.routes, "search": replace(search_route, upstream_url=mock_url)},
    )


def make_app_with_data(db_path: Path, mock_url: str):
    import app.main

    conn = connect(str(db_path))
    init_db(conn)
    create_provider(conn, "exa", "mock-upstream-key", True)
    create_provider_api_key(conn, "exa", "bench", "mock-upstream-key", enabled=True, total_quota=10_000_000)
    raw_key = "relay_benchmark_key"
    create_relay_key(
        conn,
        "bench",
        hash_secret(raw_key),
        None,
        key_value=raw_key,
        key_fingerprint=fingerprint_secret(raw_key),
    )
    conn.close()
    return app.main.create_app()


async def run_concurrency_ladder(app, base_url: str, levels: list[int], total_requests: int) -> list[dict]:
    results = []
    for level in levels:
        semaphore = asyncio.Semaphore(level)
        latencies: list[float] = []
        failures = 0

        async def one_request() -> None:
            nonlocal failures
            async with semaphore:
                started = time.perf_counter()
                try:
                    async with httpx.AsyncClient(timeout=30) as client:
                        response = await client.post(
                            f"{base_url}/exa/search?no_cache=true",
                            headers={"Authorization": "Bearer relay_benchmark_key"},
                            json=MOCK_QUERY,
                        )
                    if response.status_code != 200:
                        failures += 1
                except Exception:
                    failures += 1
                latencies.append((time.perf_counter() - started) * 1000)

        wall_started = time.perf_counter()
        await asyncio.gather(*(one_request() for _ in range(total_requests)))
        wall_seconds = time.perf_counter() - wall_started

        latencies.sort()
        p50 = statistics.median(latencies)
        p95 = latencies[int(len(latencies) * 0.95) - 1]
        p99 = latencies[int(len(latencies) * 0.99) - 1]
        results.append(
            {
                "concurrency": level,
                "p50_ms": round(p50, 1),
                "p95_ms": round(p95, 1),
                "p99_ms": round(p99, 1),
                "req_per_s": round(total_requests / wall_seconds, 1),
                "failures": failures,
            }
        )
    return results


async def run_payload_ladder(app, base_url: str, sizes_kb: list[int]) -> list[dict]:
    results = []
    for size_kb in sizes_kb:
        body = json.dumps({"query": "x" * (size_kb * 1024 // 10), "numResults": 3}).encode()
        started = time.perf_counter()
        try:
            async with httpx.AsyncClient(timeout=60) as client:
                response = await client.post(
                    f"{base_url}/exa/search?no_cache=true",
                    headers={"Authorization": "Bearer relay_benchmark_key"},
                    content=body,
                )
            duration_ms = (time.perf_counter() - started) * 1000
            status = response.status_code
        except Exception as exc:  # pragma: no cover - error path
            duration_ms = (time.perf_counter() - started) * 1000
            status = f"ERROR: {exc.__class__.__name__}"
        peak_rss_kb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        results.append(
            {
                "payload_kb": size_kb,
                "duration_ms": round(duration_ms, 1),
                "status": status,
                "peak_rss_kb": peak_rss_kb,
            }
        )
    return results


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--concurrency", default="1,10,50,100")
    parser.add_argument("--requests", type=int, default=100)
    parser.add_argument("--payloads", default="100,1024,5120,10240,20480")
    args = parser.parse_args()
    levels = [int(item) for item in args.concurrency.split(",")]
    sizes_kb = [int(item) for item in args.payloads.split(",")]

    with ExitStack() as stack:
        mock_server, mock_url = start_mock_upstream()
        stack.callback(mock_server.shutdown)
        patch_registry(mock_url)

        tmp_dir = stack.enter_context(tempfile.TemporaryDirectory(prefix="search-relay-bench-"))
        db_path = Path(tmp_dir) / "bench.sqlite3"
        os.environ.setdefault("APP_SECRET_KEY", "benchmark-secret")
        os.environ.setdefault("APP_ENV", "development")
        os.environ["APP_DATABASE_PATH"] = str(db_path)

        app = make_app_with_data(db_path, mock_url)
        port = 8787
        config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
        server = uvicorn.Server(config)
        server_thread = threading.Thread(target=server.run, daemon=True)
        server_thread.start()

        for _ in range(50):
            try:
                async with httpx.AsyncClient(timeout=2) as probe:
                    if (await probe.get(f"http://127.0.0.1:{port}/health")).status_code == 200:
                        break
            except Exception:
                await asyncio.sleep(0.1)
        else:  # pragma: no cover
            print("FATAL: relay server did not come up")
            return 1

        base_url = f"http://127.0.0.1:{port}"

        print(f"\nConcurrency ladder (mock upstream at {mock_url}, {args.requests} requests per level)")
        print(f"{'concurrency':>12} {'p50 ms':>8} {'p95 ms':>8} {'p99 ms':>8} {'req/s':>8} {'failures':>9}")
        concurrency_results = await run_concurrency_ladder(app, base_url, levels, args.requests)
        for row in concurrency_results:
            print(
                f"{row['concurrency']:>12} {row['p50_ms']:>8} {row['p95_ms']:>8} {row['p99_ms']:>8} "
                f"{row['req_per_s']:>8} {row['failures']:>9}"
            )

        print(f"\nPayload ladder ({', '.join(f'{size} KB' for size in sizes_kb)})")
        print(f"{'payload':>12} {'duration ms':>12} {'status':>8} {'peak RSS KB':>12}")
        payload_results = await run_payload_ladder(app, base_url, sizes_kb)
        for row in payload_results:
            print(
                f"{row['payload_kb']:>8} KB {row['duration_ms']:>12} {str(row['status']):>8} {row['peak_rss_kb']:>12}"
            )

        total_failures = sum(row["failures"] for row in concurrency_results)
        print()
        if total_failures == 0:
            print("VERDICT: SQLite handled the ladder cleanly — do NOT change the DB architecture.")
        else:
            print(
                f"VERDICT: {total_failures} failure(s) under concurrency — investigate WAL mode, "
                "busy_timeout, or connection management before scaling."
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
