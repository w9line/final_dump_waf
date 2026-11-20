"""Small lifecycle CLI; emergency-stop removes only Aegis-owned firewall state."""
from __future__ import annotations

import argparse
import os
import shutil
import signal
import subprocess
import time
from pathlib import Path

from .config import DEFAULT_CONFIG, load_config
from .nftables import Nftables
import asyncio


def _systemctl(action: str, units: list[str]) -> bool:
    systemctl = shutil.which("systemctl")
    if not systemctl:
        return False
    result = subprocess.run([systemctl, action, *units], check=False)
    return result.returncode == 0


def _kill_manager(config: dict) -> None:
    try:
        pid = int(Path(config["pid_file"]).read_text(encoding="ascii").strip())
    except (OSError, ValueError):
        return
    if pid <= 1 or pid == os.getpid():
        return
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return
        time.sleep(0.1)


def main() -> None:
    parser = argparse.ArgumentParser(prog="aegis")
    parser.add_argument("action", choices=("start", "stop", "emergency-stop"))
    parser.add_argument("--config", default=os.environ.get("AEGIS_CONFIG", DEFAULT_CONFIG))
    parser.add_argument("--no-systemd", action="store_true", help="use the service PID file instead of systemd")
    args = parser.parse_args()
    config = load_config(args.config)
    if args.action == "start":
        if args.no_systemd:
            raise SystemExit("start without systemd: run `python -m aegis.service --config ...`")
        if not _systemctl("start", ["aegis.service", "aegis-watchdog.service"]):
            raise SystemExit("systemctl failed to start Aegis")
        return
    if args.action == "stop":
        if args.no_systemd:
            _kill_manager(config)
        elif not _systemctl("stop", ["aegis.service", "aegis-watchdog.service"]):
            raise SystemExit("systemctl failed to stop Aegis")
    else:
        if args.no_systemd or not shutil.which("systemctl"):
            _kill_manager(config)
        else:
            _systemctl("stop", ["aegis.service", "aegis-watchdog.service"])
            _kill_manager(config)
    nft = Nftables(config["nft_command"], config["nft_table"], config["nft_chain"], int(config["nft_priority"]), int(config["listen_port"]))
    asyncio.run(nft.remove())


if __name__ == "__main__":
    main()
