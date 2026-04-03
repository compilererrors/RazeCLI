#!/usr/bin/env bash
set -euo pipefail

# Build a standalone macOS onedir bundle for faster startup than onefile.
# Output: dist/razecli-onedir/razecli-onedir

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
  --onedir \
  --name razecli-onedir \
  --collect-all hid \
  --collect-all bleak \
  --collect-submodules razecli.models \
  --collect-submodules razecli.backends \
  --collect-submodules razecli.ble \
  razecli/__main__.py

echo ""
echo "Built fast-start bundle: $ROOT_DIR/dist/razecli-onedir/razecli-onedir"
echo "Run it:"
echo "  ./dist/razecli-onedir/razecli-onedir --help"
