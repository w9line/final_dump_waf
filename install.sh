#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [[ "${EUID}" -ne 0 ]]; then
  echo "Run install.sh as root." >&2
  exit 1
fi

apt_retry() {
  local attempt
  for attempt in 1 2 3; do
    if DEBIAN_FRONTEND=noninteractive apt-get -o Acquire::Retries=3 "$@"; then
      return 0
    fi
    [[ "${attempt}" -eq 3 ]] && break
    sleep "$((attempt * 3))"
  done
  return 1
}

apt_retry update
apt_retry install -y ca-certificates curl git-lfs gnupg iproute2 nftables

# Torch 2.4.x has no Python 3.13/3.14 wheels. Prefer the newest supported ABI.
PYTHON=""
for candidate in python3.12 python3.11; do
  if command -v "${candidate}" >/dev/null 2>&1; then
    PYTHON="$(command -v "${candidate}")"
    break
  fi
done
if [[ -z "${PYTHON}" ]]; then
  apt_retry install -y software-properties-common
  if ! command -v add-apt-repository >/dev/null 2>&1; then
    echo "No Python 3.11/3.12 found and add-apt-repository is unavailable." >&2
    exit 1
  fi
  add-apt-repository -y ppa:deadsnakes/ppa
  apt_retry update
  if apt_retry install -y python3.11 python3.11-venv; then
    PYTHON="$(command -v python3.11)"
  elif apt_retry install -y python3.12 python3.12-venv; then
    PYTHON="$(command -v python3.12)"
  else
    echo "Could not install supported Python 3.11/3.12; Torch 2.4 does not support Python 3.13+." >&2
    exit 1
  fi
fi
PYTHON_VERSION="$("${PYTHON}" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')"
case "${PYTHON_VERSION}" in
  3.11|3.12) ;;
  *) echo "Unsupported Python ${PYTHON_VERSION}; use Python 3.11 or 3.12." >&2; exit 1 ;;
esac
apt_retry install -y "python${PYTHON_VERSION}-venv"

if [[ ! -e "${ROOT}/.git" ]]; then
  echo "Install from a GitHub clone so Git LFS can fetch the pinned WAF model weights." >&2
  exit 1
fi
if ! git -C "${ROOT}" -c lfs.transfer.maxretries=3 lfs pull --include="models/4thwall/model.safetensors"; then
  echo "GitHub LFS download failed after retries; check network access and Git LFS availability." >&2
  exit 1
fi
if [[ ! -s "${ROOT}/models/4thwall/model.safetensors" ]]; then
  echo "Missing WAF weights after GitHub LFS download." >&2
  exit 1
fi
install -d -m 0755 /opt/aegis /etc/aegis /var/lib/aegis /run/aegis
cp -a "${ROOT}/." /opt/aegis/
"${PYTHON}" -m venv /opt/aegis/venv
PIP=/opt/aegis/venv/bin/pip
if ! "${PIP}" install --retries 3 --timeout 30 --upgrade pip; then
  echo "Configured Python index failed; retrying pip bootstrap against PyPI." >&2
  "${PIP}" install --retries 3 --timeout 30 --index-url https://pypi.org/simple --upgrade pip
fi

if ! "${PIP}" install --retries 3 --timeout 30 --index-url https://download.pytorch.org/whl/cpu --extra-index-url https://pypi.org/simple "torch>=2.4,<2.5"; then
  echo "PyTorch CPU index unavailable; retrying the compatible Torch wheel from PyPI." >&2
  "${PIP}" install --retries 3 --timeout 30 --index-url https://pypi.org/simple "torch>=2.4,<2.5" || {
    echo "Could not install Torch 2.4.x for Python ${PYTHON_VERSION}; check supported OS/CPU architecture." >&2
    exit 1
  }
fi

if ! "${PIP}" install --retries 3 --timeout 30 /opt/aegis; then
  echo "Configured Python index failed; retrying application dependencies against PyPI and PyTorch CPU wheels." >&2
  "${PIP}" install --retries 3 --timeout 30 --index-url https://pypi.org/simple --extra-index-url https://download.pytorch.org/whl/cpu /opt/aegis || {
    echo "Dependency installation failed on both the configured index and public package indexes." >&2
    exit 1
  }
fi
if [[ ! -e /etc/aegis/config.yaml ]]; then
  install -m 0644 /opt/aegis/config.yaml /etc/aegis/config.yaml
fi
install -m 0644 /opt/aegis/systemd/aegis.service /etc/systemd/system/aegis.service
install -m 0644 /opt/aegis/systemd/aegis-watchdog.service /etc/systemd/system/aegis-watchdog.service
systemctl daemon-reload
systemctl enable aegis.service aegis-watchdog.service
echo "Installed. Review /etc/aegis/config.yaml, then run ./start.sh."
