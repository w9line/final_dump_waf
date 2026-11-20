#!/usr/bin/env python3
"""Fixed-count, fixed-worker HTTP load trial using persistent worker connections."""
from __future__ import annotations

import argparse
import http.client
import json
import resource
import statistics
import threading
import time
from concurrent.futures import ThreadPoolExecutor


def percentile(values: list[float], pct: float) -> float:
    ordered = sorted(values)
    if not ordered:
        return 0.0
    return ordered[min(len(ordered) - 1, max(0, int((pct / 100) * len(ordered) + 0.999999) - 1))]


def run_trial(host: str, port: int, count: int, concurrency: int, path_prefix: str) -> dict:
    latencies: list[float] = []
    failures: list[str] = []
    lock = threading.Lock()
    next_index = 0
    index_lock = threading.Lock()

    def worker(worker_id: int) -> None:
        nonlocal next_index
        connection = http.client.HTTPConnection(host, port, timeout=15)
        local_latencies: list[float] = []
        local_failures: list[str] = []
        while True:
            with index_lock:
                if next_index >= count:
                    break
                index = next_index
                next_index += 1
            started = time.perf_counter()
            try:
                connection.request("GET", f"{path_prefix}?n={index}", headers={"Host": "load.ctf.test"})
                response = connection.getresponse()
                response.read()
                if response.status != 200:
                    local_failures.append(f"status={response.status}")
                else:
                    local_latencies.append((time.perf_counter() - started) * 1000)
            except Exception as exc:
                local_failures.append(type(exc).__name__ + ":" + str(exc))
                connection.close()
            if connection.sock is None and not local_failures:
                # http.client reconnects lazily on the next request.
                pass
        connection.close()
        with lock:
            latencies.extend(local_latencies)
            failures.extend(local_failures)

    started = time.perf_counter()
    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        list(pool.map(worker, range(concurrency)))
    elapsed = time.perf_counter() - started
    usage = resource.getrusage(resource.RUSAGE_SELF)
    return {
        "requests_requested": count,
        "requests_completed": len(latencies),
        "failures": failures[:10],
        "concurrency": concurrency,
        "elapsed_seconds": round(elapsed, 6),
        "rps": round(len(latencies) / elapsed, 2),
        "latency_ms": {
            "p50": round(percentile(latencies, 50), 3),
            "p95": round(percentile(latencies, 95), 3),
            "p99": round(percentile(latencies, 99), 3),
        },
        "client_cpu_user_seconds": round(usage.ru_utime, 3),
        "client_cpu_system_seconds": round(usage.ru_stime, 3),
        "client_max_rss_mb": round(usage.ru_maxrss / 1024, 2),
        "errors_total": len(failures),
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="router")
    parser.add_argument("--port", type=int, default=5000)
    parser.add_argument("--requests", type=int, default=1000)
    parser.add_argument("--concurrency", type=int, default=50)
    parser.add_argument("--path", default="/load")
    parser.add_argument("--label", default="trial")
    args = parser.parse_args()
    print(json.dumps({"label": args.label, **run_trial(args.host, args.port, args.requests, args.concurrency, args.path)}, sort_keys=True))
