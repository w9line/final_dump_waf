"""mitmproxy addon for private aggregate telemetry and model-based WAF decisions."""
from __future__ import annotations

import asyncio
import json
import os
import tempfile
import time
from collections import Counter
from pathlib import Path
from typing import Any

from mitmproxy import http

from aegis.config import DEFAULT_CONFIG, load_config
from aegis.detector import ModelDetector

CONFIG = load_config(os.environ.get("AEGIS_CONFIG", DEFAULT_CONFIG))


class AegisTelemetry:
    def __init__(self) -> None:
        self.heartbeat = Path(os.environ.get("AEGIS_HEARTBEAT_FILE", "/run/aegis/proxy.json"))
        self.metrics = Path(os.environ.get("AEGIS_METRICS_FILE", "/var/lib/aegis/metrics.json"))
        self.detector = ModelDetector(CONFIG)
        self.mode = os.environ.get("AEGIS_MODE", CONFIG["mode"])
        self.requests = 0
        self.responses = 0
        self.methods: Counter[str] = Counter()
        self.host_headers: Counter[str] = Counter()
        self.original_destinations: Counter[str] = Counter()
        self.labels: Counter[str] = Counter()
        self.detector_latency_sum_ms = 0.0
        self.detector_errors = 0
        self._task: asyncio.Task | None = None
        self._started_at = time.time()

    @staticmethod
    def _atomic_json(path: Path, value: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump(value, stream, separators=(",", ":"), sort_keys=True)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
        finally:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass

    def _snapshot(self) -> dict[str, Any]:
        return {
            "pid": os.getpid(),
            "updated_at": time.time(),
            "started_at": self._started_at,
            "requests": self.requests,
            "responses": self.responses,
            "methods": dict(self.methods),
            "host_headers": dict(self.host_headers),
            "original_destinations": dict(self.original_destinations),
            "detector_labels": dict(self.labels),
            "detector_latency_sum_ms": round(self.detector_latency_sum_ms, 6),
            "detector_errors": self.detector_errors,
        }

    async def _heartbeat_loop(self) -> None:
        while True:
            snapshot = self._snapshot()
            self._atomic_json(self.heartbeat, snapshot)
            self._atomic_json(self.metrics, snapshot)
            await asyncio.sleep(1)

    def running(self) -> None:
        self._task = asyncio.get_running_loop().create_task(self._heartbeat_loop())

    def done(self) -> None:
        if self._task is not None:
            self._task.cancel()
        snapshot = self._snapshot()
        try:
            self._atomic_json(self.metrics, snapshot)
        except OSError:
            pass

    def request(self, flow: Any) -> None:
        self.requests += 1
        request = flow.request
        self.methods[request.method] += 1
        self.host_headers[str(request.headers.get("host", ""))] += 1
        try:
            result = self.detector.predict(request)
            label = str(result.get("label", "unknown"))
            self.labels[label] += 1
            self.detector_latency_sum_ms += float(result.get("latency_ms", 0.0))
            if self.mode == "block" and result.get("blocked"):
                flow.response = http.Response.make(
                    403, b"Blocked by Aegis WAF\n", {"Content-Type": "text/plain"},
                )
                self.labels["blocked"] += 1
        except Exception:
            # Model errors fail open; never turn inference failure into a block.
            self.detector_errors += 1
            self.labels["detector_error_allow"] += 1

    def response(self, flow: Any) -> None:
        self.responses += 1
        address = getattr(flow.server_conn, "address", None)
        if isinstance(address, tuple) and len(address) >= 2:
            self.original_destinations[f"{address[0]}:{address[1]}"] += 1
        else:
            request = flow.request
            self.original_destinations[f"{request.host}:{request.port}"] += 1


addons = [AegisTelemetry()]
