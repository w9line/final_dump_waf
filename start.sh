#!/usr/bin/env bash
set -euo pipefail
if [[ "${EUID}" -ne 0 ]]; then
  echo "Run start.sh as root." >&2
  exit 1
fi
exec systemctl start aegis.service aegis-watchdog.service
