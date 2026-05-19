"""Generate monster-name rosetta files from MSM SmartFox .dat/.xml payloads."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence
import xml.etree.ElementTree as ET


DAT_XOR_KEY = b"fl*&sdfgrpwvb;nlk3@$aeri03"
SOURCE_BASENAMES = ("monster_data", "gene_data", "island_data")


@dataclass(frozen=True)
class MonsterNameRosettaArtifacts:
    """Generated text payloads for the rosetta + metadata files."""

    rosetta_text: str
    metadata_text: str
    source_monster: Path
    source_gene: Path
    source_island: Path
    generated_utc: datetime
    row_count: int


def _node_scalar_value(node: ET.Element) -> str:
    if "value" in node.attrib:
        return node.attrib.get("value", "")
    return node.text or ""


def _parse_sfs_node(node: ET.Element) -> Any:
    tag = (node.tag or "").upper()
    raw = (_node_scalar_value(node) or "").strip()

    if tag in {"INT", "LONG"}:
        try:
            return int(raw)
        except Exception:
            return 0
    if tag in {"FLOAT", "DOUBLE"}:
        try:
            return float(raw)
        except Exception:
            return 0.0
    if tag in {"BOOL", "BOOLEAN"}:
        return raw.lower() in {"1", "true", "yes"}
    if tag in {"STRING", "UTFSTRING"}:
        return raw
    if tag == "NULL":
        return None
    if tag.endswith("ARRAY"):
        return [_parse_sfs_node(child) for child in list(node)]
    if tag == "SFSOBJECT":
        parsed: Dict[str, Any] = {}
        for child in list(node):
            key = child.attrib.get("key", "")
            parsed[key] = _parse_sfs_node(child)
        return parsed
    return raw


def _decode_sfs_xml_root(path: Path) -> ET.Element:
    raw = path.read_bytes()
    payloads: List[bytes] = []

    # Prefer plain XML first when this is already a dumped .xml.
    if path.suffix.lower() == ".xml" or raw.lstrip().startswith(b"<"):
        payloads.append(raw)

    # Primary encrypted format used by current game .dat files.
    decoded = bytes(byte ^ DAT_XOR_KEY[idx % len(DAT_XOR_KEY)] for idx, byte in enumerate(raw))
    payloads.append(decoded)

    # Final fallback: try raw bytes in case extension is misleading.
    if raw not in payloads:
        payloads.append(raw)

    last_error: Optional[Exception] = None
    for payload in payloads:
        try:
            text = payload.decode("utf-8")
        except UnicodeDecodeError:
            text = payload.decode("utf-8", errors="ignore")
        try:
            return ET.fromstring(text)
        except Exception as exc:
            last_error = exc

    raise ValueError(f"Failed to decode SFS XML payload: {path}") from last_error


def _load_sfs_rows(path: Path) -> List[Dict[str, Any]]:
    root = _decode_sfs_xml_root(path)
    rows: List[Dict[str, Any]] = []
    if (root.tag or "").upper() == "SFSOBJECT":
        parsed = _parse_sfs_node(root)
        if isinstance(parsed, dict):
            rows.append(parsed)
        return rows

    for child in list(root):
        if (child.tag or "").upper() != "SFSOBJECT":
            continue
        parsed = _parse_sfs_node(child)
        if isinstance(parsed, dict):
            rows.append(parsed)
    return rows


def _sanitize_text_field(value: Any) -> str:
    text = str(value or "").replace("\r", " ").replace("\n", " ").strip()
    return text


def _stringify_metadata_field(value: Any) -> str:
    text = _sanitize_text_field(value)
    if not text:
        return "-"
    return text


def _resolve_monster_gene_graphics(genes: str, gene_graphics_by_letter: Dict[str, str]) -> List[str]:
    token = _sanitize_text_field(genes).upper()
    if not token or token in {"-", "NONE", "NULL"}:
        return []

    seen: set[str] = set()
    resolved: List[str] = []
    for letter in token:
        if letter in {" ", "_", "-"}:
            continue
        graphic = _sanitize_text_field(gene_graphics_by_letter.get(letter))
        if not graphic:
            graphic = f"?{letter}"
        if graphic in seen:
            continue
        seen.add(graphic)
        resolved.append(graphic)
    return resolved


def _source_path_for_basename(root: Path, basename: str) -> Optional[Path]:
    for ext in (".dat", ".xml"):
        candidate = root / f"{basename}{ext}"
        if candidate.is_file():
            return candidate
    return None


def resolve_source_triplet(root: Path) -> Optional[Sequence[Path]]:
    """Return (monster, gene, island) files from a candidate dat/xml root."""
    if not root or not root.is_dir():
        return None
    sources: List[Path] = []
    for basename in SOURCE_BASENAMES:
        source = _source_path_for_basename(root, basename)
        if source is None:
            return None
        sources.append(source)
    return tuple(sources)


def build_rosetta_artifacts(
    source_monster: Path,
    source_gene: Path,
    source_island: Path,
    *,
    generated_utc: Optional[datetime] = None,
) -> MonsterNameRosettaArtifacts:
    """Parse source files and return rosetta + metadata text payloads."""
    generated = generated_utc or datetime.now(timezone.utc)
    if generated.tzinfo is None:
        generated = generated.replace(tzinfo=timezone.utc)
    else:
        generated = generated.astimezone(timezone.utc)

    monster_rows = _load_sfs_rows(source_monster)
    gene_rows = _load_sfs_rows(source_gene)
    island_rows = _load_sfs_rows(source_island)

    gene_rows_sorted = sorted(
        gene_rows,
        key=lambda row: (
            int(row.get("sort_order", 0) or 0),
            _sanitize_text_field(row.get("gene_letter")).upper(),
        ),
    )
    gene_graphics_by_letter: Dict[str, str] = {}
    gene_legend_lines: List[str] = []
    for gene_row in gene_rows_sorted:
        letter = _sanitize_text_field(gene_row.get("gene_letter")).upper()
        if not letter:
            continue
        graphic = _sanitize_text_field(gene_row.get("gene_graphic"))
        if graphic:
            gene_graphics_by_letter[letter] = graphic
        gene_legend_lines.append(
            (
                f"{letter} = graphic:{_stringify_metadata_field(graphic)}"
                f" | string:{_stringify_metadata_field(gene_row.get('gene_string'))}"
                f" | sort:{int(gene_row.get('sort_order', 0) or 0)}"
            )
        )

    island_membership: Dict[int, set[int]] = {}
    island_rows_sorted = sorted(island_rows, key=lambda row: int(row.get("island_id", 0) or 0))
    island_legend_lines: List[str] = []
    for island_row in island_rows_sorted:
        island_id = int(island_row.get("island_id", 0) or 0)
        if island_id <= 0:
            continue
        monsters = island_row.get("monsters") or []
        if isinstance(monsters, list):
            for monster_entry in monsters:
                if not isinstance(monster_entry, dict):
                    continue
                monster_id = int(monster_entry.get("monster", 0) or 0)
                if monster_id <= 0:
                    continue
                island_membership.setdefault(monster_id, set()).add(island_id)
        graphic = island_row.get("graphic")
        island_file = ""
        if isinstance(graphic, dict):
            island_file = _sanitize_text_field(graphic.get("file"))
        if not island_file:
            island_file = _sanitize_text_field(island_row.get("book"))
        icon_sheet = _sanitize_text_field(island_row.get("iconSheet"))
        icon_sprite = _sanitize_text_field(island_row.get("iconSprite"))
        icon_field = "-"
        if icon_sheet or icon_sprite:
            icon_field = f"{_stringify_metadata_field(icon_sheet)}:{_stringify_metadata_field(icon_sprite)}"
        island_legend_lines.append(
            (
                f"{island_id} = name:{_stringify_metadata_field(island_row.get('name'))}"
                f" | file:{_stringify_metadata_field(island_file)}"
                f" | midi:{_stringify_metadata_field(island_row.get('midi'))}"
                f" | icon:{icon_field}"
            )
        )

    rows_sorted = sorted(
        monster_rows,
        key=lambda row: (
            _sanitize_text_field(row.get("common_name")).lower(),
            _sanitize_text_field(row.get("name")).lower(),
            int(row.get("monster_id", 0) or 0),
        ),
    )

    rosetta_lines: List[str] = []
    metadata_lines: List[str] = []
    seen_pairs: set[tuple[str, str]] = set()
    seen_rows: set[tuple[str, str, int]] = set()
    for row in rows_sorted:
        common_name = _sanitize_text_field(row.get("common_name"))
        if not common_name:
            continue
        graphic = row.get("graphic")
        if not isinstance(graphic, dict):
            continue
        file_name = _sanitize_text_field(graphic.get("file"))
        if not file_name:
            continue

        pair_key = (common_name.lower(), file_name.lower())
        if pair_key not in seen_pairs:
            rosetta_lines.append(f"{common_name} = {file_name}")
            seen_pairs.add(pair_key)

        monster_id = int(row.get("monster_id", 0) or 0)
        row_key = (common_name.lower(), file_name.lower(), monster_id)
        if row_key in seen_rows:
            continue
        seen_rows.add(row_key)

        stem = Path(file_name).stem.lower()
        genes = _sanitize_text_field(row.get("genes"))
        resolved_genes = _resolve_monster_gene_graphics(genes, gene_graphics_by_letter)
        islands = sorted(island_membership.get(monster_id, set()))

        metadata_lines.append(
            " | ".join(
                [
                    f"common_name={common_name}",
                    f"file={file_name}",
                    f"stem={_stringify_metadata_field(stem)}",
                    f"entity_type={_stringify_metadata_field(row.get('entity_type'))}",
                    f"monster_id={monster_id if monster_id > 0 else '-'}",
                    f"class={_stringify_metadata_field(row.get('class'))}",
                    f"fam={_stringify_metadata_field(row.get('fam'))}",
                    f"genes={_stringify_metadata_field(genes)}",
                    (
                        "gene_graphics="
                        + (
                            ",".join(resolved_genes)
                            if resolved_genes
                            else "-"
                        )
                    ),
                    (
                        "islands="
                        + (
                            ",".join(str(value) for value in islands)
                            if islands
                            else "-"
                        )
                    ),
                ]
            )
        )

    generated_text = generated.strftime("%Y-%m-%d %H:%M:%SZ")
    metadata_sections: List[str] = [
        "# Monster resolution metadata",
        f"# Source monster_data: {source_monster}",
        f"# Source gene_data: {source_gene}",
        f"# Source island_data: {source_island}",
        f"# Generated (UTC): {generated_text}",
        "",
        "[GENE_LEGEND]",
        *gene_legend_lines,
        "",
        "[ISLAND_LEGEND]",
        *island_legend_lines,
        "",
        "[MONSTER_RESOLUTION]",
        *metadata_lines,
        "",
    ]
    metadata_text = "\n".join(metadata_sections)
    rosetta_text = "\n".join(rosetta_lines) + ("\n" if rosetta_lines else "")

    return MonsterNameRosettaArtifacts(
        rosetta_text=rosetta_text,
        metadata_text=metadata_text,
        source_monster=source_monster,
        source_gene=source_gene,
        source_island=source_island,
        generated_utc=generated,
        row_count=len(metadata_lines),
    )
