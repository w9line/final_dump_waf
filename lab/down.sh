#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${ROOT}"
docker rm -f aegis-lab-dynamic >/dev/null 2>&1 || true
docker compose -p aegis-lab -f lab/compose.yaml down --remove-orphans --volumes
