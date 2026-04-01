#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

PYTHON_BIN="${PYTHON_BIN:-python3}"
VENV_DIR="${VENV_DIR:-.venv}"
VENV_PY="${VENV_DIR}/bin/python"

ensure_pip_available() {
  local py_bin="$1"
  if "$py_bin" -m pip --version >/dev/null 2>&1; then
    return 0
  fi

  echo "pip module not found for $py_bin, attempting bootstrap with ensurepip..."
  if "$py_bin" -m ensurepip --upgrade >/dev/null 2>&1; then
    if "$py_bin" -m pip --version >/dev/null 2>&1; then
      echo "pip bootstrapped successfully."
      return 0
    fi
  fi

  echo "ERROR: pip is unavailable for $py_bin and ensurepip failed." >&2
  echo "Install the Python venv/ensurepip package for your distro, then re-run setup." >&2
  echo "Examples:" >&2
  echo "  Arch:          sudo pacman -S python-pip" >&2
  echo "  Debian/Ubuntu: sudo apt install python3-venv python3-pip" >&2
  echo "  Fedora:        sudo dnf install python3-pip" >&2
  return 1
}

echo "========================================"
echo "My Singing Monsters Animation Viewer"
echo "Linux Setup Script"
echo "========================================"
echo

if ! command -v "$PYTHON_BIN" >/dev/null 2>&1; then
  echo "ERROR: $PYTHON_BIN was not found in PATH." >&2
  echo "Install Python 3.10+ and re-run this script." >&2
  exit 1
fi

"$PYTHON_BIN" - <<'PY'
import sys
if sys.version_info < (3, 10):
    raise SystemExit("ERROR: Python 3.10 or newer is required.")
print(f"Using Python {sys.version.split()[0]}")
PY
echo

if [ ! -d "$VENV_DIR" ]; then
  echo "Creating virtual environment at $VENV_DIR..."
  if ! "$PYTHON_BIN" -m venv "$VENV_DIR"; then
    echo "ERROR: Failed to create virtual environment." >&2
    echo "Install your distro's Python venv package and try again." >&2
    exit 1
  fi
else
  echo "Using existing virtual environment at $VENV_DIR..."
fi
echo

ensure_pip_available "$VENV_PY"

echo "Upgrading pip/setuptools/wheel..."
"$VENV_PY" -m pip install --upgrade pip setuptools wheel
echo

echo "Installing viewer requirements..."
"$VENV_PY" -m pip install -r requirements.txt
echo

echo "Ensuring PSD export dependencies are available..."
"$VENV_PY" -m utils.pytoshop_installer --package pytoshop --min-version 1.2.1 --preinstall
"$VENV_PY" -m utils.pytoshop_installer --package packbits --min-version 0.1.0 --preinstall
echo

echo "Checking audio backend (PortAudio)..."
"$VENV_PY" - <<'PY'
import sys

try:
    import sounddevice as sd
except Exception as exc:
    print("[WARNING] sounddevice is installed but PortAudio is not available.")
    print(f"[WARNING] Import error: {exc}")
    print(f"[INFO] Interpreter: {sys.executable}")
    print("[INFO] Install PortAudio on your host system, then re-run setup:")
    print("       Debian/Ubuntu: sudo apt install portaudio19-dev libportaudio2")
    print("       Fedora:        sudo dnf install portaudio portaudio-devel")
    print("       Arch:          sudo pacman -S portaudio")
else:
    try:
        print(f"PortAudio backend detected: {sd.get_portaudio_version()}")
    except Exception:
        print("PortAudio backend detected.")

    try:
        devices = sd.query_devices()
        output_count = sum(1 for d in devices if int(d.get('max_output_channels', 0)) > 0)
        print(f"Detected output audio devices: {output_count}")
        if output_count <= 0:
            print("[WARNING] No output devices were reported by PortAudio.")
    except Exception as exc:
        print(f"[WARNING] Could not query PortAudio devices: {exc}")
PY
echo
echo "========================================"
echo "Setup completed successfully!"
echo "Run the viewer with: ./run_viewer.sh"
echo "========================================"
