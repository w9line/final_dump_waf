# Aegis

Aegis discovers externally bound TCP listeners, probes for plaintext HTTP, and redirects only verified HTTP ports through its own `inet aegis` nftables table to mitmproxy. Requests pass to the original destination unless model policy blocks them; replies return through the existing conntrack path. Docker-published ports are mapped to container destinations. Non-HTTP listeners and TLS ports are left untouched.

## Requirements

- Ubuntu 22.04 or 24.04, Python 3.11+, systemd, nftables, and `ss` from iproute2.
- Root privileges / `CAP_NET_ADMIN` for nftables and mitmproxy transparent mode.
- Optional Docker CLI and access to the Docker daemon for published-port/DNAT discovery.

`install.sh` installs Python 3.11 from the deadsnakes PPA on Ubuntu 22.04 when the system Python is older than 3.11. Review that repository choice in the installer before deploying to a controlled host.

## Install and operate

```sh
git clone https://github.com/w9line/final_dump_waf.git
cd final_dump_waf
sudo ./install.sh
sudoedit /etc/aegis/config.yaml
sudo ./start.sh
systemctl status aegis.service aegis-watchdog.service
```

```sh
sudo ./stop.sh
sudo ./start.sh
sudo ./emergency-stop.sh
```

`stop.sh` stops both systemd units. `emergency-stop.sh` stops them and deletes only `inet aegis`; it does not flush, rewrite, or delete any other firewall table. Aegis will not capture localhost traffic because `PREROUTING` does not see local output. HTTPS is not redirected or TLS-intercepted. The nftables chain runs after Docker's destination-NAT hook (`priority -90`) and matches container destination IP/port when Docker inspection is available.

Every discovery cycle (10 seconds by default), Aegis probes active TCP listeners and atomically replaces only its own table. A failed health check or nftables update removes the Aegis table, restoring the direct route. The watchdog runs as a separate systemd unit and checks both the supervisor and mitmproxy heartbeat files.

## Disposable real-network lab

The lab uses separate client and router network namespaces. Flask listens on `:5000`, FastAPI on `:8000`, NGINX on `:8080`, SSH on `:2222`, HTTPS on `:8443`, and integration tests start a late HTTP listener on `:31337` after Aegis.

```sh
tests/smoke.sh
AEGIS_LOAD_REQUESTS=1000 tests/integration.sh
lab/down.sh
```

The integration script exercises real nftables `PREROUTING` redirects and counters, mitmproxy original destinations, GET/POST JSON, Host/query/body/header preservation, keep-alive, dynamic discovery, SSH/HTTPS bypass, proxy-crash restart, emergency stop, watchdog fail-open, and direct-vs-Aegis load trials at concurrency 50. It prints RPS, p50/p95/p99, and container CPU/RAM snapshots. The Docker lab requires a Linux Docker engine with `NET_ADMIN` support; it can run from Docker Desktop's Linux VM.

## Configuration

`config.yaml` is the sample installation config. Important fields:

- `proxy.listen_port`: local mitmproxy port; exclude this port from the discovered listeners automatically.
- `proxy.discovery_interval_seconds`: listener scan and atomic rules update interval.
- `proxy.exclude_ports`: explicit non-HTTP/admin exclusions.
- `proxy.docker_discovery`: inspect published Docker ports and map verified host ports to backend container IP/port after Docker DNAT.
- `proxy.heartbeat_file`, `service_file`, `metrics_file`, `pid_file`: runtime health and aggregate telemetry locations.

Telemetry contains request counts, method/Host counts, destination IP:port counts, and detector latency totals. It does not log paths, request bodies, or arbitrary headers.

## Scope and operational limits

- IPv4 only; the implementation relies on mitmproxy's Linux transparent mode and `SO_ORIGINAL_DST`.
- Only inbound traffic routed through `PREROUTING` is intercepted. Loopback and local client connections bypass it.
- HTTP identification is an active but bodyless `OPTIONS *` probe. A service must return a syntactically valid HTTP/1.x status line; unknown and non-HTTP listeners remain untouched.
- The `viitheone/4thwall-waf-v2.1b` weights and tokenizer/code assets are bundled in `models/4thwall`; the 266 MB `model.safetensors` uses Git LFS. `install.sh` installs Git LFS and pulls only that weight object from this GitHub repository before copying the project to `/opt/aegis`. Inference does not download model files from Hugging Face.
- Block mode is the default. It returns 403 when any detected label matches `block_categories`; scanner reconnaissance and protocol manipulation remain alert-only unless another blocking label is also detected. Use `mode: observe` for a non-blocking rollout. The balanced test sample produced false positives; review/tune categories before exposing this policy to production traffic.
