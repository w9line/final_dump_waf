#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${ROOT}"
DC=(docker compose -p aegis-lab -f lab/compose.yaml)
"${DC[@]}" up -d --build router flask fastapi nginx ssh https client aegis watchdog
for _ in $(seq 1 300); do
  if "${DC[@]}" exec -T aegis sh -c 'test -s /run/aegis/proxy.json && nft list table inet aegis >/dev/null 2>&1' >/dev/null 2>&1; then
    break
  fi
  sleep 1
done
"${DC[@]}" exec -T client python /workspace/tests/lab_client.py
sleep 1
"${DC[@]}" exec -T aegis python /workspace/tests/lab_assert.py --minimum-requests 4
printf 'SMOKE PASS: '
"${DC[@]}" exec -T aegis python -c 'import json; m=json.load(open("/var/lib/aegis/metrics.json")); print("proxy requests=%s destinations=%s" % (m["requests"], m["original_destinations"]))'
