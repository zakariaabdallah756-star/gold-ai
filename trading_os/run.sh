#!/usr/bin/env bash
# Start the Trading OS & Journal. Creates trading_os/.venv and installs dependencies on first run.
# Usage: trading_os/run.sh [--demo] [--port 8765] [--no-browser] [--lan] [--allow-host NAME]
#   --lan              open it from your phone / other computers on your network (http://THIS-PC-IP:8765)
#   --allow-host NAME  also answer to a LAN name or tunnel, e.g. trading.lan or "*.trycloudflare.com"
set -euo pipefail
cd "$(dirname "$0")"

if [ ! -d .venv ]; then
  echo "Creating virtual environment in trading_os/.venv ..."
  python3 -m venv .venv
fi
# shellcheck disable=SC1091
source .venv/bin/activate
python -m pip install --quiet --disable-pip-version-check -r requirements.txt
exec python run.py "$@"
