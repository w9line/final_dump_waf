#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${ROOT}"
DC=(docker compose -p aegis-lab -f lab/compose.yaml)
REQUESTS="${AEGIS_LOAD_REQUESTS:-1000}"

docker rm -f aegis-lab-dynamic >/dev/null 2>&1 || true
"${DC[@]}" down --remove-orphans >/dev/null 2>&1 || true
docker rm -f aegis-lab-dynamic >/dev/null 2>&1 || true
"${DC[@]}" up -d --build router flask fastapi nginx ssh https client

wait_backends() {
  for _ in $(seq 1 60); do
    if "${DC[@]}" exec -T router python -c 'import socket; ps=(5000,8000,8080,2222,8443); [socket.create_connection(("127.0.0.1",p),1).close() for p in ps]' >/dev/null 2>&1; then
      return 0
    fi
    sleep 1
  done
  echo "Lab backends did not become ready" >&2
  exit 1
}
wait_backends

printf 'DIRECT backend load: '
"${DC[@]}" exec -T client python /workspace/lab/load_test.py --requests "${REQUESTS}" --concurrency 50 --label direct
printf 'Direct container resources after load:\n'
ROUTER_ID="$("${DC[@]}" ps -q router)"
FLASK_ID="$("${DC[@]}" ps -q flask)"
docker stats --no-stream --format '{{.Name}} cpu={{.CPUPerc}} mem={{.MemUsage}}' "${ROUTER_ID}" "${FLASK_ID}"
"${DC[@]}" up -d aegis watchdog

wait_proxy() {
  for _ in $(seq 1 60); do
    if "${DC[@]}" exec -T aegis sh -c 'test -s /run/aegis/proxy.json && nft list table inet aegis >/dev/null 2>&1' >/dev/null 2>&1; then
      return 0
    fi
    sleep 1
  done
  "${DC[@]}" logs --no-color aegis watchdog >&2 || true
  echo "Aegis proxy/nftables did not become ready" >&2
  exit 1
}
wait_proxy

"${DC[@]}" exec -T client python /workspace/tests/lab_client.py
sleep 1
"${DC[@]}" exec -T aegis python /workspace/tests/lab_assert.py --minimum-requests 4
printf 'AEGIS backend load: '
"${DC[@]}" exec -T client python /workspace/lab/load_test.py --requests "${REQUESTS}" --concurrency 50 --label aegis
printf 'Aegis container resources after load:\n'
AEGIS_ID="$("${DC[@]}" ps -q aegis)"
FLASK_ID="$("${DC[@]}" ps -q flask)"
docker stats --no-stream --format '{{.Name}} cpu={{.CPUPerc}} mem={{.MemUsage}}' "${AEGIS_ID}" "${FLASK_ID}"

# Late-bound listener must be found by the next 3-second scan and redirected.
"${DC[@]}" --profile dynamic run -d --no-deps --name aegis-lab-dynamic dynamic >/dev/null
DISCOVERED_DYNAMIC=0
for _ in $(seq 1 20); do
  if "${DC[@]}" exec -T aegis nft list table inet aegis 2>/dev/null | python3 -c 'import sys; raise SystemExit(0 if "tcp dport 31337 " in sys.stdin.read() else 1)'; then
    DISCOVERED_DYNAMIC=1
    break
  fi
  sleep 1
done
if [[ "${DISCOVERED_DYNAMIC}" -ne 1 ]]; then
  echo "Late HTTP service on port 31337 was not discovered" >&2
  exit 1
fi
"${DC[@]}" exec -T client python /workspace/tests/lab_client.py dynamic
sleep 1
"${DC[@]}" exec -T aegis python /workspace/tests/lab_assert.py --minimum-requests 5 --dynamic

BEFORE_BYPASS="$("${DC[@]}" exec -T aegis python -c 'import json; print(json.load(open("/var/lib/aegis/metrics.json"))["requests"])' | tr -d '\r')"
"${DC[@]}" exec -T client python /workspace/tests/lab_client.py bypass
sleep 1
AFTER_BYPASS="$("${DC[@]}" exec -T aegis python -c 'import json; print(json.load(open("/var/lib/aegis/metrics.json"))["requests"])' | tr -d '\r')"
if [[ "${BEFORE_BYPASS}" != "${AFTER_BYPASS}" ]]; then
  echo "SSH/HTTPS bypass unexpectedly reached the HTTP proxy" >&2
  exit 1
fi
# A mitmproxy crash is restarted by the supervisor and fresh rules are restored.
OLD_PROXY_PID="$("${DC[@]}" exec -T aegis python -c 'import json; print(json.load(open("/run/aegis/proxy.json"))["pid"])' | tr -d '\r')"
"${DC[@]}" exec -T aegis python -c "import os,signal; os.kill(${OLD_PROXY_PID}, signal.SIGKILL)"
NEW_PROXY_PID="${OLD_PROXY_PID}"
for _ in $(seq 1 40); do
  NEW_PROXY_PID="$("${DC[@]}" exec -T aegis python -c 'import json; print(json.load(open("/run/aegis/proxy.json"))["pid"])' 2>/dev/null | tr -d '\r' || true)"
  if [[ -n "${NEW_PROXY_PID}" && "${NEW_PROXY_PID}" != "${OLD_PROXY_PID}" ]] && "${DC[@]}" exec -T aegis nft list table inet aegis >/dev/null 2>&1; then
    break
  fi
  sleep 0.5
done
if [[ "${NEW_PROXY_PID}" == "${OLD_PROXY_PID}" || -z "${NEW_PROXY_PID}" ]]; then
  echo "Supervisor did not restart a crashed mitmproxy" >&2
  exit 1
fi
"${DC[@]}" exec -T client python /workspace/tests/lab_client.py

# Emergency-stop is invoked from the independent watchdog container.
"${DC[@]}" exec -T watchdog python -m aegis.cli emergency-stop --config /workspace/lab/config.yaml --no-systemd
for _ in $(seq 1 10); do
  if ! "${DC[@]}" exec -T router nft list table inet aegis >/dev/null 2>&1; then break; fi
  sleep 1
done
"${DC[@]}" exec -T client python -c 'import urllib.request; assert b"flask" in urllib.request.urlopen("http://router:5000/direct-after-emergency", timeout=5).read()'

# The lab init wrapper exits after a clean emergency stop; wait for its PID
# namespace to disappear before starting a fresh manager and watchdog.
AEGIS_RUNNING=true
for _ in $(seq 1 30); do
  AEGIS_RUNNING="$(docker inspect --format '{{.State.Running}}' aegis-lab-aegis-1)"
  if [[ "${AEGIS_RUNNING}" == "false" ]]; then break; fi
  sleep 0.1
done
if [[ "${AEGIS_RUNNING}" != "false" ]]; then
  echo "Aegis container did not stop after emergency-stop" >&2
  exit 1
fi


# Freeze the manager event loop. The independent watchdog must clear nftables,
# kill the frozen manager/proxy, and allow the lab init wrapper to restart it.
"${DC[@]}" up -d aegis watchdog >/dev/null
wait_proxy
DISCOVERED_DYNAMIC=0
for _ in $(seq 1 20); do
  if "${DC[@]}" exec -T aegis nft list table inet aegis 2>/dev/null | python3 -c 'import sys; raise SystemExit(0 if "tcp dport 31337 " in sys.stdin.read() else 1)'; then
    DISCOVERED_DYNAMIC=1
    break
  fi
  sleep 1
done
if [[ "${DISCOVERED_DYNAMIC}" -ne 1 ]]; then
  echo "Late service was not rediscovered after emergency-stop" >&2
  exit 1
fi
"${DC[@]}" exec -T client python /workspace/tests/lab_client.py dynamic
MANAGER_PID="$("${DC[@]}" exec -T aegis python -c 'import json; print(json.load(open("/run/aegis/service.json"))["pid"])' | tr -d '\r')"
"${DC[@]}" exec -T aegis python -c "import os,signal; os.kill(${MANAGER_PID}, signal.SIGSTOP)"
RECOVERED_DIRECT=0
for _ in $(seq 1 80); do
  if ! "${DC[@]}" exec -T router nft list table inet aegis >/dev/null 2>&1; then
    "${DC[@]}" exec -T client python -c 'import urllib.request; assert b"flask" in urllib.request.urlopen("http://router:5000/watchdog-fail-open", timeout=5).read()'
    RECOVERED_DIRECT=1
    break
  fi
  sleep 0.25
done
if [[ "${RECOVERED_DIRECT}" -ne 1 ]]; then
  echo "Independent watchdog did not remove redirects after manager hang" >&2
  exit 1
fi
wait_proxy
DISCOVERED_DYNAMIC=0
for _ in $(seq 1 20); do
  if "${DC[@]}" exec -T aegis nft list table inet aegis 2>/dev/null | python3 -c 'import sys; raise SystemExit(0 if "tcp dport 31337 " in sys.stdin.read() else 1)'; then
    DISCOVERED_DYNAMIC=1
    break
  fi
  sleep 1
done
if [[ "${DISCOVERED_DYNAMIC}" -ne 1 ]]; then
  echo "Late service was not rediscovered after watchdog recovery" >&2
  exit 1
fi
"${DC[@]}" exec -T client python /workspace/tests/lab_client.py
"${DC[@]}" exec -T client python /workspace/tests/lab_client.py dynamic
sleep 1
"${DC[@]}" exec -T aegis python /workspace/tests/lab_assert.py --minimum-requests 6 --dynamic

echo "INTEGRATION PASS: PREROUTING, original destination, HTTP semantics, dynamic discovery, SSH/HTTPS bypass, proxy crash recovery, emergency-stop, watchdog fail-open/recovery, and ${REQUESTS} direct/Aegis load trials"
