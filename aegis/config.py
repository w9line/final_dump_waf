"""Configuration loading and validation shared by the service and watchdog."""
from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml


DEFAULT_CONFIG = "/etc/aegis/config.yaml"


def load_config(path: str | Path = DEFAULT_CONFIG) -> dict[str, Any]:
    with Path(path).open("r", encoding="utf-8") as stream:
        document = yaml.safe_load(stream) or {}
    proxy = document.get("proxy")
    if not isinstance(proxy, dict):
        raise ValueError("config must contain a proxy mapping")
    defaults: dict[str, Any] = {
        "executable": "mitmdump",
        "listen_host": "0.0.0.0",
        "listen_port": 18081,
        "discovery_interval_seconds": 10,
        "startup_timeout_seconds": 300,
        "health_timeout_seconds": 5,
        "health_check_seconds": 1,
        "watchdog_timeout_seconds": 6,
        "exclude_ports": [22, 443, 2222, 3306, 5432, 6379, 8443, 27017],
        "ss_command": "ss",
        "docker_command": "docker",
        "docker_discovery": True,
        "nft_command": "nft",
        "nft_table": "aegis",
        "nft_chain": "prerouting",
        "nft_priority": -90,
        "heartbeat_file": "/run/aegis/proxy.json",
        "service_file": "/run/aegis/service.json",
        "metrics_file": "/var/lib/aegis/metrics.json",
        "pid_file": "/run/aegis/service.pid",
        "mitm_confdir": "/var/lib/aegis/mitm",
    }
    defaults.update(proxy)
    if not 1 <= int(defaults["listen_port"]) <= 65535:
        raise ValueError("proxy.listen_port must be between 1 and 65535")
    if defaults["nft_table"] != "aegis":
        raise ValueError("Aegis is restricted to its own inet aegis nftables table")
    for key in ("discovery_interval_seconds", "health_check_seconds", "health_timeout_seconds", "watchdog_timeout_seconds"):
        if float(defaults[key]) <= 0:
            raise ValueError(f"proxy.{key} must be positive")
    defaults["exclude_ports"] = {int(port) for port in defaults["exclude_ports"]}
    if document.get("mode", "observe") not in {"observe", "block"}:
        raise ValueError("mode must be observe or block")
    defaults["mode"] = document.get("mode", "observe")
    defaults["block_categories"] = list(document.get("block_categories", []))
    return defaults
