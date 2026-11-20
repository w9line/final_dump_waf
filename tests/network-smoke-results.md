# Network smoke results

## Environment

- Docker Desktop Linux VM, ARM64.
- mitmproxy 12.3.1; nftables 1.1.3; Python 3.12 lab image.
- Isolated router and separate client network namespaces.

## Observed smoke

- Flask `:5000`, FastAPI `:8000`, and NGINX `:8080` requests reached mitmproxy and the original backends.
- Proxy telemetry reported 5 requests and 5 responses.
- `SO_ORIGINAL_DST` destinations observed through mitmproxy were `172.22.0.2:5000` (2), `172.22.0.2:8000` (1), and `172.22.0.2:8080` (2).
- GET, POST JSON, Host, query, extra headers, NGINX keep-alive, HTTPS `:8443` direct TLS, and SSH `:2222` banner checks passed.
- Backend counters observed: Flask 3, FastAPI 3 after test-side direct counter reads.

The full dynamic-discovery, crash-recovery, emergency-stop, watchdog, and load integration run is still in progress; this file records only the completed smoke evidence.
