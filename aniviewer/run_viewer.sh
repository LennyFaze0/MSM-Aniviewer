#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

if [ -x "$SCRIPT_DIR/.venv/bin/python" ]; then
  PYTHON_BIN="$SCRIPT_DIR/.venv/bin/python"
else
  PYTHON_BIN="${PYTHON_BIN:-python3}"
fi

if [ ! -x "$PYTHON_BIN" ] && ! command -v "$PYTHON_BIN" >/dev/null 2>&1; then
  echo "Error: $PYTHON_BIN not found in PATH." >&2
  echo "Run ./setup_linux.sh first, or install Python 3.10+." >&2
  exit 1
fi

if [ -n "${MSM_AUDIO_DEVICE:-}" ]; then
  echo "Using forced audio device selector: $MSM_AUDIO_DEVICE"
fi

exec "$PYTHON_BIN" main.py "$@"
