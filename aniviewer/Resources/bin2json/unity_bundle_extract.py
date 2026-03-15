#!/usr/bin/env python
"""
UnityFS bundle lister/extractor using UnityPy.

Examples:
  python unity_bundle_extract.py list <bundle_or_dir>
  python unity_bundle_extract.py extract <bundle_or_dir> --out out_dir --types MonoBehaviour,Texture2D
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Iterable, List, Optional, Set, Tuple

import UnityPy


UNITYFS_MAGIC = (b"UnityFS", b"UnityWeb", b"UnityRaw")


def _is_unityfs(path: Path) -> bool:
    try:
        with path.open("rb") as handle:
            head = handle.read(8)
        return any(head.startswith(tag) for tag in UNITYFS_MAGIC)
    except OSError:
        return False


def _find_bundles(path: Path) -> List[Path]:
    if path.is_file():
        return [path]
    bundles: List[Path] = []
    for root, _, files in os.walk(path):
        for name in files:
            candidate = Path(root) / name
            if _is_unityfs(candidate):
                bundles.append(candidate)
    return bundles


def _normalize_types(raw: Optional[str]) -> Set[str]:
    if not raw:
        return set()
    return {item.strip() for item in raw.split(",") if item.strip()}


def _safe_name(value: str) -> str:
    keep = []
    for ch in value:
        if ch.isalnum() or ch in ("_", "-", "."):
            keep.append(ch)
        else:
            keep.append("_")
    return "".join(keep)[:180] or "unnamed"


def _object_name(obj, typetree: Optional[dict]) -> str:
    if typetree:
        name = typetree.get("m_Name") or typetree.get("name")
        if isinstance(name, str) and name.strip():
            return name
    try:
        data = obj.read()
        name = getattr(data, "m_Name", None) or getattr(data, "name", None)
        if isinstance(name, str) and name.strip():
            return name
    except Exception:
        pass
    return f"pathid_{obj.path_id}"


def list_bundle(bundle: Path, type_filter: Set[str]) -> List[Tuple[str, int, str]]:
    env = UnityPy.load(str(bundle))
    rows: List[Tuple[str, int, str]] = []
    for obj in env.objects:
        type_name = getattr(obj.type, "name", str(obj.type))
        if type_filter and type_name not in type_filter:
            continue
        try:
            name = _object_name(obj, None)
        except Exception:
            name = f"pathid_{obj.path_id}"
        rows.append((type_name, obj.path_id, name))
    return rows


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=True, indent=2)


def extract_bundle(
    bundle: Path,
    out_dir: Path,
    type_filter: Set[str],
    name_filter: Optional[str],
    write_raw: bool,
) -> int:
    env = UnityPy.load(str(bundle))
    count = 0
    for obj in env.objects:
        type_name = getattr(obj.type, "name", str(obj.type))
        if type_filter and type_name not in type_filter:
            continue
        typetree = None
        try:
            typetree = obj.read_typetree()
        except Exception:
            typetree = None
        name = _object_name(obj, typetree)
        if name_filter and name_filter.lower() not in name.lower():
            continue
        safe = _safe_name(name)
        base = out_dir / type_name / safe
        if typetree:
            _write_json(base.with_suffix(".json"), typetree)
            count += 1
        if type_name == "Texture2D":
            try:
                data = obj.read()
                img = data.image
                if img:
                    img_path = base.with_suffix(".png")
                    img_path.parent.mkdir(parents=True, exist_ok=True)
                    img.save(img_path)
                    count += 1
            except Exception:
                pass
        elif type_name == "TextAsset":
            try:
                data = obj.read()
                text = getattr(data, "script", None)
                if isinstance(text, (bytes, bytearray)):
                    text = text.decode("utf-8", errors="replace")
                if isinstance(text, str):
                    txt_path = base.with_suffix(".txt")
                    txt_path.parent.mkdir(parents=True, exist_ok=True)
                    txt_path.write_text(text, encoding="utf-8")
                    count += 1
            except Exception:
                pass
        if write_raw:
            try:
                raw = obj.get_raw_data()
                raw_path = base.with_suffix(".bin")
                raw_path.parent.mkdir(parents=True, exist_ok=True)
                raw_path.write_bytes(raw)
                count += 1
            except Exception:
                pass
    return count


def main() -> int:
    parser = argparse.ArgumentParser(description="UnityFS bundle lister/extractor (UnityPy).")
    sub = parser.add_subparsers(dest="cmd", required=True)

    list_cmd = sub.add_parser("list", help="List objects in bundles.")
    list_cmd.add_argument("path", help="Bundle file or directory.")
    list_cmd.add_argument("--types", default="", help="Comma-separated type filter.")

    extract_cmd = sub.add_parser("extract", help="Extract objects from bundles.")
    extract_cmd.add_argument("path", help="Bundle file or directory.")
    extract_cmd.add_argument("--out", required=True, help="Output directory.")
    extract_cmd.add_argument("--types", default="", help="Comma-separated type filter.")
    extract_cmd.add_argument("--name-contains", default="", help="Filter by name substring.")
    extract_cmd.add_argument("--raw", action="store_true", help="Also dump raw binary for each object.")

    args = parser.parse_args()
    base = Path(args.path)
    bundles = _find_bundles(base)
    if not bundles:
        print(f"No UnityFS bundles found at: {base}")
        return 2
    type_filter = _normalize_types(getattr(args, "types", ""))

    if args.cmd == "list":
        for bundle in bundles:
            rows = list_bundle(bundle, type_filter)
            print(f"{bundle} ({len(rows)} objects)")
            for type_name, path_id, name in rows:
                print(f"  {type_name:16} {path_id:>18} {name}")
        return 0

    out_dir = Path(args.out)
    name_filter = args.name_contains.strip() if args.name_contains else None
    total = 0
    for bundle in bundles:
        count = extract_bundle(bundle, out_dir, type_filter, name_filter, args.raw)
        print(f"{bundle}: extracted {count} entries")
        total += count
    print(f"Total extracted entries: {total}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
