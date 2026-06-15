#!/usr/bin/env bash
# Convenience launcher: create a venv, install deps, and start MarketSim.
set -euo pipefail
cd "$(dirname "$0")"

if [ ! -d .venv ]; then
  python3 -m venv .venv
fi
# shellcheck disable=SC1091
source .venv/bin/activate
pip install -q -r requirements.txt

PORT="${1:-8000}"
echo "Starting MarketSim on http://127.0.0.1:${PORT}"
python -m marketsim.cli serve --port "${PORT}"
