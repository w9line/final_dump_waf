"""Manage only the inet aegis table; all updates are nftables transactions."""
from __future__ import annotations

import asyncio
import ipaddress
import logging
from dataclasses import dataclass
from typing import Iterable

log = logging.getLogger(__name__)


@dataclass(frozen=True, order=True)
class CaptureTarget:
    port: int
    address: str | None = None
    service: str = "http"


class Nftables:
    def __init__(self, command: str, table: str, chain: str, priority: int, proxy_port: int):
        self.command = command
        self.table = table
        self.chain = chain
        self.priority = priority
        self.proxy_port = proxy_port

    async def _has_owned_table(self) -> bool:
        proc = await asyncio.create_subprocess_exec(
            self.command, "list", "table", "inet", self.table,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        if proc.returncode == 0:
            if 'comment "aegis-managed"' not in stdout.decode(errors="replace"):
                raise RuntimeError(f"Refusing to modify pre-existing inet {self.table} without Aegis ownership marker")
            return True
        detail = stderr.decode(errors="replace").strip()
        if "No such file or directory" in detail:
            return False
        raise RuntimeError(f"Cannot inspect inet {self.table}: {detail}")

    def _validate(self, targets: Iterable[CaptureTarget]) -> list[CaptureTarget]:
        result: dict[tuple[int, str | None], CaptureTarget] = {}
        for target in targets:
            if not 1 <= target.port <= 65535 or target.port == self.proxy_port:
                continue
            address = target.address
            if address is not None:
                parsed = ipaddress.ip_address(address)
                if parsed.version != 4 or parsed.is_loopback or parsed.is_unspecified or parsed.is_multicast:
                    continue
                address = str(parsed)
            result[(target.port, address)] = CaptureTarget(target.port, address, target.service)
        return sorted(result.values())

    async def replace(self, targets: Iterable[CaptureTarget]) -> None:
        safe_targets = self._validate(targets)
        existing = await self._has_owned_table()
        lines = []
        if existing:
            lines.append(f"delete table inet {self.table}")
        lines.extend((
            f'add table inet {self.table} {{ comment "aegis-managed"; }}',
            f"add chain inet {self.table} {self.chain} {{ type nat hook prerouting priority {self.priority}; policy accept; }}",
        ))
        for target in safe_targets:
            parts = ["meta nfproto ipv4"]
            if target.address:
                parts.extend(("ip daddr", target.address))
            parts.extend(("tcp dport", str(target.port), "counter", f"redirect to :{self.proxy_port}"))
            lines.append(f"add rule inet {self.table} {self.chain} {' '.join(parts)}")
        payload = ("\n".join(lines) + "\n").encode()
        proc = await asyncio.create_subprocess_exec(
            self.command, "-f", "-", stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await proc.communicate(payload)
        if proc.returncode:
            raise RuntimeError(f"nftables transaction failed: {stderr.decode(errors='replace').strip()}")
        log.info("Installed %d HTTP capture targets in inet %s", len(safe_targets), self.table)

    async def remove(self) -> None:
        if not await self._has_owned_table():
            return
        proc = await asyncio.create_subprocess_exec(
            self.command, "delete", "table", "inet", self.table,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await proc.communicate()
        if proc.returncode:
            detail = stderr.decode(errors="replace").strip()
            if "No such file or directory" not in detail:
                raise RuntimeError(f"Could not remove owned inet {self.table}: {detail}")
