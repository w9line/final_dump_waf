"""Independent fail-open watchdog for stale service or mitmproxy heartbeats."""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import signal
import time
from pathlib import Path
from typing import Any

from .config import DEFAULT_CONFIG, load_config
from .nftables import Nftables

log = logging.getLogger("aegis.watchdog")


class Watchdog:
    def __init__(self, config: dict[str, Any]):
        self.config = config
        self.stopping = asyncio.Event()
        self.service_file = Path(config["service_file"])
        self.heartbeat_file = Path(config["heartbeat_file"])
        self.timeout = float(config["watchdog_timeout_seconds"])
        self.startup_timeout = float(config["startup_timeout_seconds"])
        self.nft = Nftables(config["nft_command"], config["nft_table"], config["nft_chain"], int(config["nft_priority"]), int(config["listen_port"]))
        self.triggered_for: tuple[int, int] | None = None

    @staticmethod
    def _read(path: Path) -> dict[str, Any] | None:
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        return value if isinstance(value, dict) else None

    @staticmethod
    def _matches_process(pid: int, markers: tuple[bytes, ...]) -> bool:
        try:
            command = Path(f"/proc/{pid}/cmdline").read_bytes()
        except OSError:
            return False
        return any(marker in command for marker in markers)


    async def _recover(self, service: dict[str, Any], signature: tuple[int, int]) -> None:
        await self.nft.remove()
        log.critical("Stale Aegis heartbeat; removed only inet aegis and restarting the service")
        proxy_pid = int(service.get("proxy_pid", 0) or 0)
        service_pid = int(service.get("pid", 0) or 0)
        proxy_name = Path(str(self.config["executable"])).name.encode()
        for pid, markers in (
            (proxy_pid, (proxy_name,)),
            (service_pid, (b"aegis.service", b"aegis-service")),
        ):
            if pid <= 1 or pid == os.getpid() or not self._matches_process(pid, markers):
                continue
            try:
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            except PermissionError:
                log.exception("Cannot terminate stale Aegis PID %s", pid)
        self.triggered_for = signature

    async def run(self) -> None:
        while not self.stopping.is_set():
            service = self._read(self.service_file)
            if service:
                service_pid = int(service.get("pid", 0) or 0)
                updated = float(service.get("updated_at", 0) or 0)
                proxy = self._read(self.heartbeat_file)
                proxy_updated = float((proxy or {}).get("updated_at", 0) or 0)
                proxy_pid = int((proxy or {}).get("pid", 0) or 0)
                proxy_expected = int(service.get("proxy_pid", 0) or 0)
                stale = time.time() - updated > self.timeout
                startup_age = time.time() - float(service.get("proxy_started_at") or service.get("started_at") or updated)
                if proxy_expected and (proxy is None or proxy_pid != proxy_expected):
                    stale = stale or startup_age > self.startup_timeout + self.timeout
                elif proxy_expected:
                    stale = stale or time.time() - proxy_updated > self.timeout
                signature = (service_pid, proxy_expected)
                if not stale:
                    self.triggered_for = None
                elif self.triggered_for != signature:
                    await self._recover(service, signature)
            else:
                # Aegis may have died before its graceful cleanup. With no live
                # supervisor there is no safe proxy to send traffic to.
                await self.nft.remove()
                self.triggered_for = None
            try:
                await asyncio.wait_for(self.stopping.wait(), timeout=1.0)
            except asyncio.TimeoutError:
                pass


def main() -> None:
    parser = argparse.ArgumentParser(description="Aegis fail-open watchdog")
    parser.add_argument("--config", default=os.environ.get("AEGIS_CONFIG", DEFAULT_CONFIG))
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    watchdog = Watchdog(load_config(args.config))
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, watchdog.stopping.set)
    try:
        loop.run_until_complete(watchdog.run())
    finally:
        loop.close()


if __name__ == "__main__":
    main()
