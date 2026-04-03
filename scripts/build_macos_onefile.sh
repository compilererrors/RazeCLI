#!/usr/bin/env bash
set -euo pipefail

# Build a standalone one-file macOS binary for RazeCLI.
# Output: dist/razecli

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

if [[ ! -x ".venv/bin/python" ]]; then
  echo "Missing .venv. Create it first:"
  echo "  python3 -m venv .venv"
  echo "  .venv/bin/python -m pip install -U pip"
  echo "  .venv/bin/python -m pip install -e \".[detect,ble,bundle]\""
  exit 1
fi

PYTHON=".venv/bin/python"

"$PYTHON" -m pip install -e ".[detect,ble,bundle]"

# PYTHONPYCACHEPREFIX avoids macOS cache permission problems in some environments.
PYTHONPYCACHEPREFIX=/tmp/razecli-pyc "$PYTHON" -m PyInstaller \
  --noconfirm \
  --clean \
  --onefile \
  --name razecli \
  --collect-all hid \
  --collect-all bleak \
  --collect-all CoreBluetooth \
  --collect-all Foundation \
  --collect-all libdispatch \
  --collect-all objc \
  --collect-submodules bleak.backends.corebluetooth \
  --hidden-import bleak.backends.corebluetooth.CentralManagerDelegate \
  --hidden-import bleak.backends.corebluetooth.PeripheralDelegate \
  --hidden-import bleak.backends.corebluetooth.client \
  --hidden-import bleak.backends.corebluetooth.scanner \
  --hidden-import bleak.backends.corebluetooth.utils \
  --hidden-import CoreBluetooth \
  --hidden-import Foundation \
  --hidden-import libdispatch \
  --hidden-import objc \
  --collect-submodules razecli.models \
  --collect-submodules razecli.backends \
  --collect-submodules razecli.ble \
  razecli/__main__.py

echo ""
echo "Built binary: $ROOT_DIR/dist/razecli"
echo "Run it:"
echo "  ./dist/razecli --help"
