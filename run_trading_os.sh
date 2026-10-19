#!/usr/bin/env bash
# Start the Trading OS & Journal. Creates .venv and installs dependencies on first run.
# Usage: ./run_trading_os.sh [--demo] [--port 8765] [--no-browser]
set -euo pipefail
cd "$(dirname "$0")"

if [ ! -d .venv ]; then
  echo "Creating virtual environment in .venv ..."
  python3 -m venv .venv
fi
# shellcheck disable=SC1091
source .venv/bin/activate
python -m pip install --quiet --disable-pip-version-check -r trading_os/requirements.txt
exec python run_trading_os.py "$@"
