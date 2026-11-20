#!/usr/bin/env python3
"""Assert proxy telemetry, original destinations, nft counters, and backend counters."""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import time
import urllib.request
from pathlib import Path


def read_metrics(path: str, timeout: float = 5) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            value = json.loads(Path(path).read_text(encoding="utf-8"))
            if isinstance(value, dict):
                return value
        except (OSError, json.JSONDecodeError):
            pass
        time.sleep(0.2)
    raise AssertionError(f"no valid proxy metrics at {path}")


def nft_table() -> str:
    result = subprocess.run(["nft", "list", "table", "inet", "aegis"], check=True, text=True, capture_output=True)
    return result.stdout


def assert_counter(table: str, port: int, positive: bool = True) -> None:
    rows = [row for row in table.splitlines() if f"tcp dport {port} " in row]
    assert rows, f"missing nft redirect rule for port {port}"
    if positive:
        assert any(re.search(r"counter packets [1-9][0-9]*", row) for row in rows), f"nft counter stayed at zero for port {port}: {rows}"


def backend_counter(port: int) -> int:
    with urllib.request.urlopen(f"http://127.0.0.1:{port}/__counter", timeout=4) as response:
        value = json.loads(response.read())
    return int(value["count"])


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--metrics", default="/var/lib/aegis/metrics.json")
    parser.add_argument("--minimum-requests", type=int, default=4)
    parser.add_argument("--dynamic", action="store_true")
    args = parser.parse_args()
    metrics = read_metrics(args.metrics)
    assert metrics["requests"] >= args.minimum_requests, metrics
    assert metrics["methods"].get("GET", 0) >= 1 and metrics["methods"].get("POST", 0) >= 2, metrics
    table = nft_table()
    for port in (5000, 8000, 8080):
        assert_counter(table, port, positive=not args.dynamic)
    if args.dynamic:
        assert_counter(table, 31337)
    for port in (2222, 8443):
        assert f"tcp dport {port} " not in table, f"non-HTTP service was intercepted: {port}"
    original = metrics.get("original_destinations", {})
    for port in (5000, 8000, 8080):
        assert any(key.endswith(f":{port}") for key in original), f"original destination :{port} absent: {original}"
    assert backend_counter(5000) >= 3, "Flask backend counter did not advance"
    assert backend_counter(8000) >= 1, "FastAPI backend counter did not advance"
    if args.dynamic:
        assert any(key.endswith(":31337") for key in original), f"dynamic original destination missing: {original}"
    print(json.dumps({
        "requests": metrics["requests"],
        "responses": metrics["responses"],
        "methods": metrics["methods"],
        "original_destinations": original,
        "backend_counts": {"flask": backend_counter(5000), "fastapi": backend_counter(8000)},
        "nft_redirect_ports": [5000, 8000, 8080] + ([31337] if args.dynamic else []),
    }, sort_keys=True))
