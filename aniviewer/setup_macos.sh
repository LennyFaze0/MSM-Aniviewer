#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

PYTHON_BIN="${PYTHON_BIN:-python3}"

echo "========================================"
echo "My Singing Monsters Animation Viewer"
echo "macOS Setup Script"
echo "========================================"
echo

echo "Checking Python installation..."
if ! "$PYTHON_BIN" --version >/dev/null 2>&1; then
  echo "ERROR: Python 3.10 or newer is required. Install it from python.org or Homebrew and re-run this script."
  exit 1
fi
echo

echo "Upgrading pip and installing requirements..."
"$PYTHON_BIN" -m pip install --upgrade pip
"$PYTHON_BIN" -m pip install -r requirements.txt
echo

echo "Ensuring PSD export dependencies are available..."
"$PYTHON_BIN" -m utils.pytoshop_installer --package pytoshop --min-version 1.2.1 --preinstall
"$PYTHON_BIN" -m utils.pytoshop_installer --package packbits --min-version 0.1.0 --preinstall
echo

echo "========================================"
echo "Setup completed successfully!"
echo "Run the viewer with: ./run_viewer.sh"
echo "========================================"
