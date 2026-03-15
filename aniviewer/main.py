"""
My Singing Monsters Animation Viewer
Main entry point for the application

A comprehensive animation viewer with OpenGL rendering, timeline scrubbing, and export features.
"""

import os
import runpy
import sys
from pathlib import Path


def _maybe_run_embedded_script() -> bool:
    """Allow the packaged executable to run helper scripts (bin2json, etc.)."""
    if len(sys.argv) >= 3 and sys.argv[1] == "--run-script":
        script_path = Path(sys.argv[2])
        if not script_path.exists():
            raise FileNotFoundError(f"Embedded script not found: {script_path}")
        # Rebuild argv for the target script
        sys.argv = [str(script_path)] + sys.argv[3:]
        script_dir = script_path.parent
        if str(script_dir) not in sys.path:
            sys.path.insert(0, str(script_dir))
        runpy.run_path(str(script_path), run_name="__main__")
        return True
    return False


def _print_boot_progress(step: int, total: int, message: str) -> None:
    bar_len = 36
    clamped = max(0, min(step, total))
    filled = int(round(bar_len * (clamped / max(1, total))))
    bar = "=" * filled + "-" * (bar_len - filled)
    text = f"[{bar}] {clamped}/{total} {message}"
    # Pad so shorter messages overwrite previous content
    print("\r" + text.ljust(120), end="", flush=True)
    if clamped >= total:
        print()


def main() -> int:
    """Main entry point"""
    steps = [
        "Initializing Qt",
        "Loading UI modules",
        "Creating application",
        "Building main window",
        "Showing window",
        "Ready",
    ]
    total = len(steps)
    _print_boot_progress(1, total, steps[0])

    from PyQt6.QtWidgets import QApplication

    _print_boot_progress(2, total, steps[1])
    from ui.main_window import MSMAnimationViewer

    _print_boot_progress(3, total, steps[2])
    app = QApplication(sys.argv)
    app.setStyle('Fusion')

    _print_boot_progress(4, total, steps[3])
    window = MSMAnimationViewer()

    _print_boot_progress(5, total, steps[4])
    window.show()

    _print_boot_progress(6, total, steps[5])
    exit_code = app.exec()
    return exit_code


if __name__ == '__main__':
    if _maybe_run_embedded_script():
        sys.exit(0)
    code = main()
    # Forcefully terminate to avoid lingering non-daemon threads (audio, sounddevice, etc.)
    os._exit(code)
