"""Discover only listeners which answer a harmless, bounded HTTP probe."""
from __future__ import annotations

import asyncio
import ipaddress
import json
import logging
import shutil
import re
from dataclasses import dataclass

from .nftables import CaptureTarget

log = logging.getLogger(__name__)
_STATUS = re.compile(rb"^HTTP/1\.[01] [0-9]{3}(?: |\r?$)")


@dataclass(frozen=True)
class Listener:
    host: str
    port: int


def parse_ss_listeners(output: str) -> set[Listener]:
    listeners: set[Listener] = set()
    for row in output.splitlines():
        fields = row.split()
        if len(fields) < 4 or fields[0] != "LISTEN":
            continue
        endpoint = fields[3]
        if endpoint.startswith("[") and "]:" in endpoint:
            host, raw_port = endpoint[1:].rsplit("]:", 1)
        elif ":" in endpoint:
            host, raw_port = endpoint.rsplit(":", 1)
        else:
            continue
        try:
            port = int(raw_port)
        except ValueError:
            continue
        if not 1 <= port <= 65535:
            continue
        # Ignore IPv6-only addresses. An unspecified IPv6 socket is probed over
        # IPv4 and captured only when it actually accepts that connection.
        if host not in ("*", "0.0.0.0", "::"):
            try:
                address = ipaddress.ip_address(host)
            except ValueError:
                continue
            if address.version != 4 or address.is_loopback or address.is_link_local or address.is_multicast:
                continue
        listeners.add(Listener(host, port))
    return listeners


async def _http_probe(host: str, port: int, timeout: float = 1.0) -> bool:
    if host in ("*", "0.0.0.0", "::"):
        host = "127.0.0.1"
    writer: asyncio.StreamWriter | None = None
    try:
        reader, writer = await asyncio.wait_for(asyncio.open_connection(host, port), timeout)
        writer.write(b"OPTIONS * HTTP/1.0\r\nHost: aegis-probe.invalid\r\nConnection: close\r\n\r\n")
        await asyncio.wait_for(writer.drain(), timeout)
        status = await asyncio.wait_for(reader.readline(), timeout)
        return bool(_STATUS.match(status))
    except (asyncio.TimeoutError, OSError, asyncio.IncompleteReadError, ConnectionError):
        return False
    finally:
        if writer is not None:
            writer.close()
            try:
                await asyncio.wait_for(writer.wait_closed(), timeout)
            except (asyncio.TimeoutError, ConnectionError, OSError):
                pass


async def _run_json(command: list[str], timeout: float = 3.0) -> str:
    proc = await asyncio.create_subprocess_exec(
        *command, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
    )
    try:
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        return ""
    return stdout.decode(errors="replace") if proc.returncode == 0 else ""


async def _docker_targets(docker_command: str, excluded: set[int], proxy_port: int) -> set[CaptureTarget]:
    if shutil.which(docker_command) is None:
        return set()
    ids = (await _run_json([docker_command, "ps", "-q"])).split()
    targets: set[CaptureTarget] = set()
    probes: dict[tuple[str, int], bool] = {}
    for container_id in ids:
        raw = await _run_json([docker_command, "inspect", "--format", "{{json .NetworkSettings}}", container_id])
        try:
            network = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            continue
        ports = network.get("Ports") or {}
        ips = [item.get("IPAddress", "") for item in (network.get("Networks") or {}).values()]
        backend_ips = [ip for ip in ips if ip and _valid_backend_ip(ip)]
        for binding, mappings in ports.items():
            if not binding or not binding.endswith("/tcp") or not mappings:
                continue
            try:
                inner_port = int(binding.split("/", 1)[0])
            except ValueError:
                continue
            for mapping in mappings:
                try:
                    external_port = int(mapping["HostPort"])
                except (KeyError, TypeError, ValueError):
                    continue
                host_ip = mapping.get("HostIp", "0.0.0.0")
                try:
                    if ipaddress.ip_address(host_ip).is_loopback:
                        continue
                except ValueError:
                    continue
                if external_port in excluded or inner_port in excluded or external_port == proxy_port or inner_port == proxy_port:
                    continue
                probe_key = ("127.0.0.1", external_port)
                if probe_key not in probes:
                    probes[probe_key] = await _http_probe(*probe_key)
                if probes[probe_key]:
                    for backend_ip in backend_ips:
                        targets.add(CaptureTarget(inner_port, backend_ip, f"docker:{container_id[:12]}"))
    return targets


def _valid_backend_ip(value: str) -> bool:
    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        return False
    return address.version == 4 and not (address.is_loopback or address.is_unspecified or address.is_link_local or address.is_multicast)


async def discover_http_targets(
    ss_command: str,
    docker_command: str,
    docker_enabled: bool,
    excluded_ports: set[int],
    proxy_port: int,
) -> set[CaptureTarget]:
    output = await _run_json([ss_command, "-H", "-ltn"])
    if not output:
        raise RuntimeError(f"{ss_command} -H -ltn failed or returned no listeners")
    listeners = parse_ss_listeners(output)
    candidates = [item for item in listeners if item.port not in excluded_ports and item.port != proxy_port]
    sem = asyncio.Semaphore(24)

    async def probe(listener: Listener) -> bool:
        async with sem:
            return await _http_probe(listener.host, listener.port)

    detected = await asyncio.gather(*(probe(listener) for listener in candidates))
    targets: set[CaptureTarget] = set()
    for listener, is_http in zip(candidates, detected):
        if not is_http:
            continue
        address = listener.host
        if address in ("*", "0.0.0.0", "::"):
            address = None
        targets.add(CaptureTarget(listener.port, address, "ss"))
    if docker_enabled:
        targets.update(await _docker_targets(docker_command, excluded_ports, proxy_port))
    return targets
