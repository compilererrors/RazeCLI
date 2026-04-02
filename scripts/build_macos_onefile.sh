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
  echo "  .venv/bin/python -m pip install -e \".[detect,bundle]\""
  exit 1
fi

PYTHON=".venv/bin/python"

"$PYTHON" -m pip install -e ".[detect,bundle]"

# PYTHONPYCACHEPREFIX avoids macOS cache permission problems in some environments.
PYTHONPYCACHEPREFIX=/tmp/razecli-pyc "$PYTHON" -m PyInstaller \
  --noconfirm \
  --clean \
  --onefile \
  --name razecli \
  --collect-all hid \
  razecli/__main__.py

echo ""
echo "Built binary: $ROOT_DIR/dist/razecli"
echo "Run it:"
echo "  ./dist/razecli --help"

