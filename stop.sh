#!/usr/bin/env bash
set -euo pipefail
if [[ "${EUID}" -ne 0 ]]; then
  echo "Run stop.sh as root." >&2
  exit 1
fi
exec /opt/aegis/venv/bin/aegis stop --config /etc/aegis/config.yaml
