#!/usr/bin/env bash
# Trading OS & Journal — headless launch script (v1.8).
#
# Starts the app bound to every interface, with no browser, for a VPS or a daemon. It is used by the systemd
# unit (trading-os.service) and works standalone too. Configure it with environment variables:
#
#   TRADING_OS_DATA_DIR        where the database, screenshots and backups live   (default: ./trading_os_data)
#   TRADING_OS_PORT            port to listen on                                   (default: 8765)
#   TRADING_OS_HOST            bind address                                        (default: 0.0.0.0)
#   TRADING_OS_ALLOWED_HOSTS   comma-separated domain names (IPs are always ok)    (default: empty)
#   TRADING_OS_VENV            path to a virtualenv to activate                    (default: <repo>/.venv if present)
#
# The app still only answers to localhost, IP addresses and TRADING_OS_ALLOWED_HOSTS — binding 0.0.0.0 only
# makes the port reachable.

set -euo pipefail

# Resolve the repository root (this script lives in trading_os/deploy/).
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
cd "${REPO_ROOT}"

DATA_DIR="${TRADING_OS_DATA_DIR:-${REPO_ROOT}/trading_os_data}"
PORT="${TRADING_OS_PORT:-8765}"
HOST="${TRADING_OS_HOST:-0.0.0.0}"
VENV="${TRADING_OS_VENV:-${REPO_ROOT}/.venv}"

export TRADING_OS_DATA_DIR="${DATA_DIR}"
# TRADING_OS_ALLOWED_HOSTS is read directly by the app from the environment; leave it as-is.

if [ -f "${VENV}/bin/activate" ]; then
  # shellcheck disable=SC1091
  . "${VENV}/bin/activate"
fi

mkdir -p "${DATA_DIR}"

echo "Starting Trading OS on ${HOST}:${PORT} (data: ${DATA_DIR})"
exec python trading_os/run.py --host "${HOST}" --port "${PORT}" --no-browser --data-dir "${DATA_DIR}"
