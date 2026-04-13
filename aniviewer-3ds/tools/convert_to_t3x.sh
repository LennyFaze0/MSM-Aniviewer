#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat << 'USAGE'
Usage:
  convert_to_t3x.sh <input_file_or_directory> [output_directory]

Supported input formats:
  - .png
  - .avif

Examples:
  convert_to_t3x.sh /path/to/monster_sheet.avif
  convert_to_t3x.sh /path/to/atlas_pngs /path/to/out_t3x

Notes:
  - Requires `tex3ds`.
  - For AVIF decode, script tries: avifdec -> magick -> ffmpeg.
USAGE
}

need_cmd() {
  if ! command -v "$1" >/dev/null 2>&1; then
    echo "[ERROR] Missing required command: $1" >&2
    exit 1
  fi
}

lower_ext() {
  local p="$1"
  local b="${p##*/}"
  local e="${b##*.}"
  printf '%s' "${e,,}"
}

decode_avif_to_png() {
  local in_avif="$1"
  local out_png="$2"

  if command -v avifdec >/dev/null 2>&1; then
    avifdec "$in_avif" "$out_png" >/dev/null
    return 0
  fi

  if command -v magick >/dev/null 2>&1; then
    magick "$in_avif" "$out_png"
    return 0
  fi

  if command -v ffmpeg >/dev/null 2>&1; then
    ffmpeg -y -loglevel error -i "$in_avif" "$out_png"
    return 0
  fi

  return 1
}

convert_one() {
  local src="$1"
  local out="$2"
  local tmp_png=""
  local ext

  ext="$(lower_ext "$src")"
  mkdir -p "$(dirname "$out")"

  case "$ext" in
    png)
      tex3ds -f rgba8 -z auto -o "$out" "$src" >/dev/null
      ;;
    avif)
      tmp_png="$(mktemp --suffix=.png)"
      if ! decode_avif_to_png "$src" "$tmp_png"; then
        rm -f "$tmp_png"
        echo "[ERROR] Could not decode AVIF: $src" >&2
        return 1
      fi
      tex3ds -f rgba8 -z auto -o "$out" "$tmp_png" >/dev/null
      rm -f "$tmp_png"
      ;;
    *)
      echo "[WARN] Skipping unsupported file: $src"
      ;;
  esac
}

main() {
  if [[ "${1:-}" == "-h" || "${1:-}" == "--help" || $# -lt 1 ]]; then
    usage
    exit 0
  fi

  need_cmd tex3ds

  local input="$1"
  local outdir="${2:-}"
  local count=0

  if [[ ! -e "$input" ]]; then
    echo "[ERROR] Input path does not exist: $input" >&2
    exit 1
  fi

  if [[ -z "$outdir" ]]; then
    if [[ -d "$input" ]]; then
      outdir="$input"
    else
      outdir="$(dirname "$input")"
    fi
  fi

  mkdir -p "$outdir"

  if [[ -f "$input" ]]; then
    local base="$(basename "$input")"
    local stem="${base%.*}"
    convert_one "$input" "$outdir/$stem.t3x"
    echo "[OK] $input -> $outdir/$stem.t3x"
    exit 0
  fi

  while IFS= read -r -d '' f; do
    rel="${f#"$input"/}"
    stem="${rel%.*}"
    ext="$(lower_ext "$f")"
    out="$outdir/$stem.t3x"
    if [[ -e "$out" ]]; then
      out="$outdir/${stem}__${ext}.t3x"
    fi
    convert_one "$f" "$out"
    echo "[OK] $f -> $out"
    count=$((count + 1))
  done < <(find "$input" -type f \( -iname '*.png' -o -iname '*.avif' \) -print0)

  echo "[DONE] Converted $count file(s)."
}

main "$@"
