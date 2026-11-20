#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [[ "${EUID}" -ne 0 ]]; then
  echo "Run install.sh as root." >&2
  exit 1
fi

apt-get update
apt-get install -y ca-certificates curl gnupg iproute2 nftables python3-venv python3-pip
PYTHON="$(command -v python3)"
if ! "${PYTHON}" -c 'import sys; raise SystemExit(sys.version_info < (3, 11))'; then
  apt-get install -y software-properties-common
  add-apt-repository -y ppa:deadsnakes/ppa
  apt-get update
  apt-get install -y python3.11 python3.11-venv
  PYTHON="$(command -v python3.11)"
fi

install -d -m 0755 /opt/aegis /etc/aegis /var/lib/aegis /run/aegis
cp -a "${ROOT}/." /opt/aegis/
"${PYTHON}" -m venv /opt/aegis/venv
/opt/aegis/venv/bin/pip install --upgrade pip
/opt/aegis/venv/bin/pip install /opt/aegis
if [[ ! -e /etc/aegis/config.yaml ]]; then
  install -m 0644 /opt/aegis/config.yaml /etc/aegis/config.yaml
fi
install -m 0644 /opt/aegis/systemd/aegis.service /etc/systemd/system/aegis.service
install -m 0644 /opt/aegis/systemd/aegis-watchdog.service /etc/systemd/system/aegis-watchdog.service
systemctl daemon-reload
systemctl enable aegis.service aegis-watchdog.service
echo "Installed. Review /etc/aegis/config.yaml, then run ./start.sh."
