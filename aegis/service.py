"""Proxy supervisor: fail open on health or nftables errors, then restart mitmproxy."""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import shutil
import signal
import time
from pathlib import Path
from typing import Any

from .config import DEFAULT_CONFIG, load_config
from .discovery import discover_http_targets
from .nftables import CaptureTarget, Nftables

log = logging.getLogger("aegis.service")


class Service:
    def __init__(self, config: dict[str, Any]):
        self.config = config
        self.proxy = config
        self.proxy_proc: asyncio.subprocess.Process | None = None
        self.stopping = asyncio.Event()
        self.applied_targets: set[CaptureTarget] | None = None
        self.nft = Nftables(
            config["nft_command"], config["nft_table"], config["nft_chain"],
            int(config["nft_priority"]), int(config["listen_port"]),
        )
        self.service_file = Path(config["service_file"])
        self.pid_file = Path(config["pid_file"])
        self.heartbeat_file = Path(config["heartbeat_file"])
        self.proxy_started_at: float | None = None
        self.started_at = time.time()

    @staticmethod
    def _read_json(path: Path) -> dict[str, Any] | None:
        try:
            with path.open("r", encoding="utf-8") as stream:
                data = json.load(stream)
            return data if isinstance(data, dict) else None
        except (OSError, json.JSONDecodeError):
            return None

    def _write_status(self) -> None:
        heartbeat = self._read_json(self.heartbeat_file) or {}
        self.service_file.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.service_file.with_suffix(".tmp")
        temporary.write_text(json.dumps({
            "pid": os.getpid(),
            "proxy_pid": self.proxy_proc.pid if self.proxy_proc else None,
            "started_at": self.started_at,
            "proxy_started_at": self.proxy_started_at,
            "updated_at": time.time(),
            "proxy_updated_at": heartbeat.get("updated_at"),
        }, separators=(",", ":")) + "\n", encoding="utf-8")
        os.replace(temporary, self.service_file)

    def _proxy_healthy(self) -> bool:
        heartbeat = self._read_json(self.heartbeat_file)
        if not heartbeat:
            return False
        if heartbeat.get("pid") != (self.proxy_proc.pid if self.proxy_proc else None):
            return False
        return time.time() - float(heartbeat.get("updated_at", 0)) <= float(self.proxy["health_timeout_seconds"])

    async def _start_proxy(self) -> bool:
        executable = self.proxy["executable"]
        if os.path.sep not in executable:
            executable = shutil.which(executable) or executable
        Path(self.proxy["mitm_confdir"]).mkdir(parents=True, exist_ok=True)
        env = os.environ.copy()
        env.update({
            "AEGIS_HEARTBEAT_FILE": str(self.heartbeat_file),
            "AEGIS_METRICS_FILE": str(self.proxy["metrics_file"]),
            "AEGIS_CONFIG": os.environ.get("AEGIS_CONFIG", DEFAULT_CONFIG),
            "HF_HOME": "/var/lib/aegis/huggingface",
        })
        addon = str(Path(__file__).with_name("addon.py"))
        argv = [
            executable, "-q", "--mode", "transparent",
            "--listen-host", str(self.proxy["listen_host"]),
            "--listen-port", str(self.proxy["listen_port"]),
            "--set", f"confdir={self.proxy['mitm_confdir']}",
            "-s", addon,
        ]
        self.proxy_started_at = time.time()
        try:
            self.heartbeat_file.unlink()
        except FileNotFoundError:
            pass
        self.proxy_proc = await asyncio.create_subprocess_exec(*argv, env=env)
        deadline = time.monotonic() + float(self.proxy["startup_timeout_seconds"])
        while time.monotonic() < deadline and not self.stopping.is_set():
            if self.proxy_proc.returncode is not None:
                log.error("mitmproxy exited during startup with status %s", self.proxy_proc.returncode)
                return False
            self._write_status()
            if self._proxy_healthy():
                log.info("mitmproxy heartbeat is healthy (pid %s)", self.proxy_proc.pid)
                return True
            await asyncio.sleep(0.25)
        log.error("mitmproxy did not become healthy before startup timeout")
        return False

    async def _stop_proxy(self) -> None:
        process = self.proxy_proc
        self.proxy_proc = None
        self.applied_targets = None
        if process is None or process.returncode is not None:
            return
        process.terminate()
        try:
            await asyncio.wait_for(process.wait(), timeout=3)
        except asyncio.TimeoutError:
            process.kill()
            await process.wait()

    async def _discover_and_apply(self) -> None:
        targets = await discover_http_targets(
            self.proxy["ss_command"], self.proxy["docker_command"],
            bool(self.proxy["docker_discovery"]), self.proxy["exclude_ports"],
            int(self.proxy["listen_port"]),
        )
        if targets == self.applied_targets:
            return
        try:
            await self.nft.replace(targets)
        except Exception:
            log.exception("nftables update failed; removing only inet aegis to fail open")
            self.applied_targets = None
            await self.nft.remove()
            return
        log.info("HTTP discovery updated to %d eligible destination(s)", len(targets))
        self.applied_targets = targets

    async def run(self) -> None:
        self.service_file.parent.mkdir(parents=True, exist_ok=True)
        self.pid_file.parent.mkdir(parents=True, exist_ok=True)
        self.pid_file.write_text(f"{os.getpid()}\n", encoding="ascii")
        await self.nft.remove()
        restart_delay = 0.5
        try:
            while not self.stopping.is_set():
                started = await self._start_proxy()
                if not started:
                    await self.nft.remove()
                    await self._stop_proxy()
                    self._write_status()
                    try:
                        await asyncio.wait_for(self.stopping.wait(), timeout=restart_delay)
                    except asyncio.TimeoutError:
                        pass
                    restart_delay = min(restart_delay * 2, 5.0)
                    continue
                restart_delay = 0.5
                next_discovery = 0.0
                failed = False
                while not self.stopping.is_set():
                    self._write_status()
                    if self.proxy_proc is None or self.proxy_proc.returncode is not None or not self._proxy_healthy():
                        log.error("Proxy process or event-loop heartbeat failed; restoring direct routing")
                        await self.nft.remove()
                        failed = True
                        break
                    now = time.monotonic()
                    if now >= next_discovery:
                        try:
                            await self._discover_and_apply()
                        except Exception:
                            log.exception("Listener discovery failed; restoring direct routing")
                            await self.nft.remove()
                        next_discovery = time.monotonic() + float(self.proxy["discovery_interval_seconds"])
                    try:
                        await asyncio.wait_for(self.stopping.wait(), timeout=float(self.proxy["health_check_seconds"]))
                    except asyncio.TimeoutError:
                        continue
                if self.stopping.is_set():
                    break
                if failed:
                    await self._stop_proxy()
                    try:
                        await asyncio.wait_for(self.stopping.wait(), timeout=restart_delay)
                    except asyncio.TimeoutError:
                        pass
                    restart_delay = min(restart_delay * 2, 5.0)
        finally:
            await self.nft.remove()
            await self._stop_proxy()
            for path in (self.service_file, self.pid_file):
                try:
                    path.unlink()
                except FileNotFoundError:
                    pass


def main() -> None:
    parser = argparse.ArgumentParser(description="Aegis transparent proxy supervisor")
    parser.add_argument("--config", default=os.environ.get("AEGIS_CONFIG", DEFAULT_CONFIG))
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    config = load_config(args.config)
    service = Service(config)
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, service.stopping.set)
    try:
        loop.run_until_complete(service.run())
    finally:
        loop.close()


if __name__ == "__main__":
    main()
