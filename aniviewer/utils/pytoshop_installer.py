"""
Generic PyPI installer that prefers ready-made wheels but can fall back to
building from source. Used for pytoshop/packbits so users do not need VS build
tools or VS Code just to export PSDs.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path
from typing import Callable, Dict, Optional


def _default_logger(message: str, level: str = "INFO") -> None:
    print(f"[{level}] {message}")


class PythonPackageInstaller:
    """Download/install a PyPI package, preferring universal wheels."""

    def __init__(
        self,
        package_name: str,
        requirement_spec: Optional[str] = None,
        log_fn: Optional[Callable[[str, str], None]] = None,
    ):
        self.package_name = package_name
        self.requirement_spec = requirement_spec or package_name
        self.log = log_fn or _default_logger
        self._frozen = bool(getattr(sys, "frozen", False))
        exe = sys.executable or "python"
        self.python_exe = Path(exe)
        self.cache_dir = self._resolve_cache_dir() / package_name
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def install_latest(self, requested_version: Optional[str] = None) -> bool:
        if self._frozen:
            self.log(
                f"{self.package_name}: package installs are disabled in the packaged build.",
                "ERROR",
            )
            return False
        metadata = self._fetch_metadata()
        if not metadata:
            self.log(f"{self.package_name}: unable to query PyPI metadata, falling back to pip.", "WARNING")
            return self._pip_install_direct(self.requirement_spec)

        version = requested_version or metadata["info"]["version"]
        wheel_path = self.cache_dir / f"{self.package_name}-{version}.whl"
        if wheel_path.exists():
            self.log(f"{self.package_name}: using cached wheel {wheel_path.name}", "INFO")
            return self._install_wheel(wheel_path)

        urls = metadata.get("urls", [])
        wheel_info = self._select_artifact(urls, "bdist_wheel")
        if wheel_info:
            self.log(f"{self.package_name}: downloading wheel {wheel_info['filename']}", "INFO")
            if self._download_file(wheel_info["url"], wheel_path):
                return self._install_wheel(wheel_path)
            self.log(f"{self.package_name}: wheel download failed, attempting sdist build", "WARNING")

        sdist_info = self._select_artifact(urls, "sdist")
        if sdist_info:
            built_wheel = self._build_from_sdist(sdist_info["url"], version)
            if built_wheel and built_wheel.exists():
                return self._install_wheel(built_wheel)

        self.log(f"{self.package_name}: automatic wheel build failed; using pip install.", "WARNING")
        spec = f"{self.package_name}=={version}" if version else self.requirement_spec
        return self._pip_install_direct(spec)

    # ------------------------------------------------------------------ #

    def _resolve_cache_dir(self) -> Path:
        root = (
            Path(os.environ["LOCALAPPDATA"])
            if os.environ.get("LOCALAPPDATA")
            else Path.home() / ".msm_anim_viewer"
        )
        return root / "deps"

    def _fetch_metadata(self) -> Optional[Dict]:
        url = f"https://pypi.org/pypi/{self.package_name}/json"
        try:
            with urllib.request.urlopen(url, timeout=20) as resp:
                data = resp.read().decode("utf-8")
                return json.loads(data)
        except Exception as exc:
            self.log(f"{self.package_name}: metadata fetch failed: {exc}", "ERROR")
            return None

    @staticmethod
    def _select_artifact(urls: list, packagetype: str) -> Optional[Dict]:
        candidates = [entry for entry in urls if entry.get("packagetype") == packagetype]
        if not candidates:
            return None
        if packagetype == "bdist_wheel":
            for entry in candidates:
                filename = entry.get("filename", "")
                if filename.endswith("py3-none-any.whl"):
                    return entry
        return candidates[0]

    def _download_file(self, url: str, dest: Path) -> bool:
        try:
            with urllib.request.urlopen(url, timeout=60) as resp, open(dest, "wb") as handle:
                shutil.copyfileobj(resp, handle)
            return True
        except Exception as exc:
            self.log(f"{self.package_name}: download failed ({exc})", "ERROR")
            if dest.exists():
                dest.unlink()
            return False

    def _build_from_sdist(self, url: str, version: str) -> Optional[Path]:
        tmp_dir = Path(tempfile.mkdtemp(prefix=f"{self.package_name}_src_"))
        try:
            archive_path = tmp_dir / Path(url).name
            if not self._download_file(url, archive_path):
                return None
            cmd = [
                str(self.python_exe),
                "-m",
                "pip",
                "wheel",
                str(archive_path),
                "--no-deps",
                "--wheel-dir",
                str(self.cache_dir),
            ]
            self.log(f"{self.package_name}: building wheel from source...", "INFO")
            subprocess.check_call(cmd)
            wheel = self._locate_built_wheel(version)
            if wheel:
                self.log(f"{self.package_name}: built wheel {wheel.name}", "SUCCESS")
                return wheel
        except subprocess.CalledProcessError as exc:
            self.log(f"{self.package_name}: wheel build failed ({exc})", "ERROR")
            return None
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)
        return None

    def _locate_built_wheel(self, version: str) -> Optional[Path]:
        prefix = f"{self.package_name}-{version}"
        for wheel in self.cache_dir.glob(f"{prefix}*.whl"):
            return wheel
        wheels = list(self.cache_dir.glob(f"{self.package_name}-*.whl"))
        return wheels[0] if wheels else None

    def _install_wheel(self, wheel_path: Path) -> bool:
        cmd = [
            str(self.python_exe),
            "-m",
            "pip",
            "install",
            "--upgrade",
            "--no-deps",
            str(wheel_path),
        ]
        self.log(f"{self.package_name}: installing wheel...", "INFO")
        try:
            subprocess.check_call(cmd)
            self.log(f"{self.package_name}: installed from {wheel_path.name}", "SUCCESS")
            return True
        except subprocess.CalledProcessError as exc:
            self.log(f"{self.package_name}: wheel install failed ({exc})", "ERROR")
            return False

    def _pip_install_direct(self, spec: str) -> bool:
        cmd = [str(self.python_exe), "-m", "pip", "install", spec]
        try:
            subprocess.check_call(cmd)
            self.log(f"{self.package_name}: installed via pip ({spec})", "SUCCESS")
            return True
        except subprocess.CalledProcessError as exc:
            self.log(f"{self.package_name}: pip install failed ({exc})", "ERROR")
            return False


class PytoshopInstaller(PythonPackageInstaller):
    def __init__(self, log_fn: Optional[Callable[[str, str], None]] = None):
        super().__init__("pytoshop", "pytoshop>=1.2.1", log_fn)


def main(argv: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser(description="PyPI wheel installer helper")
    parser.add_argument("--package", default="pytoshop", help="Package name (default: pytoshop)")
    parser.add_argument(
        "--requirement",
        default=None,
        help="Optional requirement spec (e.g., 'packbits>=0.1.0'). Defaults to package name.",
    )
    parser.add_argument("--version", help="Explicit version to install (defaults to latest).")
    parser.add_argument(
        "--min-version",
        dest="min_version",
        help="Optional minimum version; used only if --requirement is not provided.",
    )
    parser.add_argument("--preinstall", action="store_true", help="Compatibility flag; ignored.")
    args = parser.parse_args(argv)
    requirement = args.requirement
    if requirement is None and args.min_version:
        requirement = f"{args.package}>={args.min_version}"
    if args.package.lower() == "pytoshop" and args.requirement is None:
        installer = PytoshopInstaller()
    else:
        installer = PythonPackageInstaller(args.package, requirement)
    return 0 if installer.install_latest(args.version) else 1


if __name__ == "__main__":
    raise SystemExit(main())
