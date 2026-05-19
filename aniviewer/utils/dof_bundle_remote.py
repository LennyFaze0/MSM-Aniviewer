"""
Remote DOF bundle manifest + download helpers.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple
from urllib.parse import quote
from urllib.request import Request, urlopen

DEFAULT_DOF_CDN_BASE_URL = "https://dlc2.bbbgame.net/monster_babies/dlc"
SUPPORTED_DOF_BUNDLE_PLATFORMS = ("ios", "android")


class DofBundleRemoteError(RuntimeError):
    """Raised when remote DOF bundle discovery/download fails."""


@dataclass(frozen=True)
class DofBundleManifestEntry:
    """One bundle record from bundles.json."""

    name: str
    size: int
    url: str


def normalize_dof_cdn_base_url(base_url: str) -> str:
    """Normalize base URL for manifest/bundle construction."""
    base = str(base_url or "").strip()
    if not base:
        return DEFAULT_DOF_CDN_BASE_URL
    return base.rstrip("/")


def normalize_dof_bundle_platform(platform: str) -> str:
    """Normalize platform token used by the CDN path."""
    value = str(platform or "").strip().lower()
    return value if value in SUPPORTED_DOF_BUNDLE_PLATFORMS else "ios"


def build_dof_manifest_url(base_url: str, build_key: str, platform: str) -> str:
    """Build the bundles.json URL."""
    key = str(build_key or "").strip()
    if not key:
        raise DofBundleRemoteError("Build key is required.")
    base = normalize_dof_cdn_base_url(base_url)
    platform_norm = normalize_dof_bundle_platform(platform)
    return f"{base}/{quote(key, safe='-_.~')}/{platform_norm}/bundles.json"


def build_dof_bundle_url(base_url: str, build_key: str, platform: str, bundle_name: str) -> str:
    """Build a bundle file URL."""
    key = str(build_key or "").strip()
    name = str(bundle_name or "").strip()
    if not key:
        raise DofBundleRemoteError("Build key is required.")
    if not name:
        raise DofBundleRemoteError("Bundle name is required.")
    base = normalize_dof_cdn_base_url(base_url)
    platform_norm = normalize_dof_bundle_platform(platform)
    return (
        f"{base}/{quote(key, safe='-_.~')}/{platform_norm}/"
        f"{quote(name, safe='-_.~')}"
    )


def fetch_dof_bundle_manifest(
    base_url: str,
    build_key: str,
    platform: str,
    timeout: float = 20.0,
) -> Tuple[str, List[DofBundleManifestEntry]]:
    """
    Fetch and parse DOF bundles.json.

    Returns:
        (manifest_url, sorted_entries)
    """
    manifest_url = build_dof_manifest_url(base_url, build_key, platform)
    request = Request(manifest_url, headers={"User-Agent": "MSMAnimationViewer/DOF"})
    try:
        with urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except Exception as exc:
        raise DofBundleRemoteError(f"Failed to fetch manifest: {exc}") from exc

    entries: List[DofBundleManifestEntry] = []
    if isinstance(payload, dict):
        iterable: Iterable[Tuple[str, Any]] = payload.items()
        for name, meta in iterable:
            if not isinstance(name, str) or not name.strip():
                continue
            size = _coerce_manifest_size(meta)
            url = build_dof_bundle_url(base_url, build_key, platform, name)
            entries.append(DofBundleManifestEntry(name=name, size=size, url=url))
    elif isinstance(payload, list):
        for item in payload:
            if not isinstance(item, dict):
                continue
            name_raw = item.get("name") or item.get("bundle") or item.get("path")
            if not isinstance(name_raw, str) or not name_raw.strip():
                continue
            name = name_raw.strip()
            size = _coerce_manifest_size(item)
            url = build_dof_bundle_url(base_url, build_key, platform, name)
            entries.append(DofBundleManifestEntry(name=name, size=size, url=url))
    else:
        raise DofBundleRemoteError(
            f"Unsupported manifest format: expected object/list, got {type(payload).__name__}."
        )

    entries.sort(key=lambda entry: entry.name.lower())
    return manifest_url, entries


def download_dof_bundles(
    entries: List[DofBundleManifestEntry],
    target_dir: str,
    selected_names: Optional[Iterable[str]] = None,
    *,
    skip_existing: bool = True,
    timeout: float = 60.0,
    progress_callback: Optional[Callable[[str, int, int], None]] = None,
) -> Dict[str, str]:
    """
    Download selected bundles into target_dir.

    Returns:
        mapping of bundle_name -> local_path for files present after download.
    """
    root = os.path.normpath(str(target_dir or "").strip())
    if not root:
        raise DofBundleRemoteError("Target directory is required.")
    os.makedirs(root, exist_ok=True)

    selected = {str(name).strip() for name in selected_names or [] if str(name).strip()}
    download_all = not selected
    by_name = {entry.name: entry for entry in entries}
    names = list(by_name.keys()) if download_all else [name for name in by_name.keys() if name in selected]
    if not names:
        raise DofBundleRemoteError("No bundle names selected.")

    downloaded: Dict[str, str] = {}
    for name in names:
        entry = by_name[name]
        dest_path = os.path.join(root, name)
        if skip_existing and os.path.isfile(dest_path):
            downloaded[name] = dest_path
            continue
        _download_file(entry.url, dest_path, timeout=timeout, progress_callback=progress_callback, label=name)
        downloaded[name] = dest_path
    return downloaded


def _coerce_manifest_size(meta: Any) -> int:
    if isinstance(meta, dict):
        value = meta.get("size", -1)
    else:
        value = -1
    try:
        size_int = int(value)
    except Exception:
        size_int = -1
    return size_int if size_int >= 0 else -1


def _download_file(
    url: str,
    dest_path: str,
    *,
    timeout: float,
    progress_callback: Optional[Callable[[str, int, int], None]],
    label: str,
    chunk_size: int = 256 * 1024,
) -> None:
    tmp_path = f"{dest_path}.part"
    os.makedirs(os.path.dirname(dest_path) or ".", exist_ok=True)
    request = Request(url, headers={"User-Agent": "MSMAnimationViewer/DOF"})
    try:
        with urlopen(request, timeout=timeout) as response:
            total = _parse_content_length(response.headers.get("Content-Length"))
            downloaded = 0
            with open(tmp_path, "wb") as handle:
                while True:
                    chunk = response.read(chunk_size)
                    if not chunk:
                        break
                    handle.write(chunk)
                    downloaded += len(chunk)
                    if progress_callback is not None:
                        progress_callback(label, downloaded, total)
    except Exception as exc:
        try:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
        except Exception:
            pass
        raise DofBundleRemoteError(f"Failed to download '{label}': {exc}") from exc

    os.replace(tmp_path, dest_path)


def _parse_content_length(value: Any) -> int:
    try:
        parsed = int(value)
    except Exception:
        return -1
    return parsed if parsed >= 0 else -1

