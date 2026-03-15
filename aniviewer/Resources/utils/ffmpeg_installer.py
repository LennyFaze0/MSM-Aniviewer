"""
FFmpeg installer helpers

Provides helpers to download a vetted FFmpeg build, extract it into a
user-writable directory, and update the user's PATH so the binary can
be discovered by subprocess/shutil.which calls.
"""

from __future__ import annotations

import os
import shutil
import tempfile
import urllib.request
import zipfile
from pathlib import Path
from typing import Callable, Optional

FFMPEG_DOWNLOAD_URL = "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip"
EXECUTABLE_NAME = "ffmpeg.exe" if os.name == "nt" else "ffmpeg"

StatusCallback = Optional[Callable[[str], None]]
ProgressCallback = Optional[Callable[[int], None]]


def get_install_root() -> Path:
    """Return the directory that will contain the extracted FFmpeg build."""
    base_dir = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA") or str(Path.home())
    return Path(base_dir) / "MSMAnimationViewer" / "ffmpeg"


def get_bin_dir(root: Optional[Path] = None) -> Path:
    """Return the bin directory within the local install."""
    root = root or get_install_root()
    return root / "bin"


def get_executable_path(root: Optional[Path] = None) -> Path:
    """Return the FFmpeg executable path inside the managed install."""
    return get_bin_dir(root) / EXECUTABLE_NAME


def has_local_install() -> bool:
    """Return True if the managed FFmpeg install exists."""
    return get_executable_path().exists()


def resolve_ffmpeg_path(preferred_path: Optional[str] = None) -> Optional[str]:
    """
    Resolve a usable FFmpeg binary path.

    Order of precedence:
      1. preferred_path if it exists
      2. managed local install (LOCALAPPDATA/MSMAnimationViewer/ffmpeg/bin)
      3. Anything found on PATH
    """
    if preferred_path:
        preferred = Path(preferred_path)
        if preferred.exists():
            return str(preferred)

    local_install = get_executable_path()
    if local_install.exists():
        return str(local_install)

    system_exe = shutil.which("ffmpeg")
    return system_exe


def install_ffmpeg(
    status_callback: StatusCallback = None,
    progress_callback: ProgressCallback = None
) -> str:
    """
    Download and install FFmpeg, returning the path to ffmpeg.exe.

    Args:
        status_callback: Optional callback for human-readable status updates.
        progress_callback: Optional callback receiving an integer 0-100.

    Raises:
        RuntimeError: if installation fails for any reason.
    """
    tmp_dir = Path(tempfile.mkdtemp(prefix="msm_ffmpeg_"))
    archive_path = tmp_dir / "ffmpeg.zip"

    try:
        _emit(status_callback, "Downloading FFmpeg...")
        _download_file(FFMPEG_DOWNLOAD_URL, archive_path, progress_callback)
        _emit(status_callback, "Extracting archive...")
        extracted_dir = _extract_archive(archive_path, tmp_dir)

        install_root = get_install_root()
        if install_root.exists():
            shutil.rmtree(install_root)
        install_root.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(extracted_dir), str(install_root))

        exe_path = get_executable_path(install_root)
        if not exe_path.exists():
            raise RuntimeError("FFmpeg executable not found after extraction.")

        added_to_path = _ensure_path_contains(exe_path.parent)
        _ensure_process_path(exe_path.parent)

        if progress_callback:
            progress_callback(100)

        if added_to_path:
            _emit(status_callback, "FFmpeg installed and PATH updated.")
        else:
            _emit(status_callback, "FFmpeg installed.")

        return str(exe_path)
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def _emit(callback: StatusCallback, message: str):
    if callback:
        callback(message)


def _download_file(url: str, destination: Path, progress_callback: ProgressCallback):
    chunk_size = 1024 * 256
    with urllib.request.urlopen(url) as response, open(destination, "wb") as out_file:
        total = int(response.headers.get("Content-Length", "0") or 0)
        downloaded = 0

        while True:
            chunk = response.read(chunk_size)
            if not chunk:
                break

            out_file.write(chunk)
            downloaded += len(chunk)

            if total and progress_callback:
                progress = max(1, int(downloaded / total * 100))
                progress_callback(min(progress, 99))


def _extract_archive(archive_path: Path, destination: Path) -> Path:
    with zipfile.ZipFile(archive_path) as archive:
        archive.extractall(destination)

    candidates = [
        item for item in destination.iterdir()
        if item.is_dir() and not item.name.startswith("__MACOSX")
    ]

    if not candidates:
        raise RuntimeError("Unable to find extracted FFmpeg directory.")

    for candidate in candidates:
        if candidate.name.lower().startswith("ffmpeg"):
            return candidate

    return candidates[0]


def _ensure_process_path(bin_dir: Path):
    """Ensure the current process sees the new bin directory."""
    bin_str = str(bin_dir)
    current = os.environ.get("PATH", "")
    paths = [segment.strip() for segment in current.split(os.pathsep) if segment.strip()]

    if any(os.path.normcase(p) == os.path.normcase(bin_str) for p in paths):
        return

    os.environ["PATH"] = f"{bin_str}{os.pathsep}{current}" if current else bin_str


def _ensure_path_contains(bin_dir: Path) -> bool:
    """
    Add bin_dir to the user's PATH, returning True if an update was made.
    On non-Windows platforms this is a no-op that returns False.
    """
    if os.name != "nt":
        return False

    bin_str = str(bin_dir)

    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment", 0, winreg.KEY_ALL_ACCESS) as key:
            try:
                current_value, reg_type = winreg.QueryValueEx(key, "Path")
            except FileNotFoundError:
                current_value, reg_type = "", winreg.REG_EXPAND_SZ

            segments = [
                segment.strip()
                for segment in current_value.split(";")
                if segment.strip()
            ]

            if any(os.path.normcase(segment) == os.path.normcase(bin_str) for segment in segments):
                return False

            segments.append(bin_str)
            new_value = ";".join(segments)
            winreg.SetValueEx(key, "Path", 0, reg_type, new_value)

        _broadcast_env_update()
        return True
    except Exception as exc:  # pragma: no cover - best effort
        print(f"Warning: Failed to update user PATH: {exc}")
        return False


def _broadcast_env_update():
    if os.name != "nt":
        return

    try:
        import ctypes

        HWND_BROADCAST = 0xFFFF
        WM_SETTINGCHANGE = 0x001A
        ctypes.windll.user32.SendMessageTimeoutW(
            HWND_BROADCAST,
            WM_SETTINGCHANGE,
            0,
            "Environment",
            0,
            1000,
            None
        )
    except Exception:
        pass
