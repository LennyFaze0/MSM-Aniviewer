#!/usr/bin/env bash
set -euo pipefail

# Maintainer-only asset conversion; neither the website nor CI needs ImageMagick.
APP=$(cd "$(dirname "$0")/.." && pwd)
SOURCE="$APP/../build_assets/aniviewer program icon.ico"
OUTPUT="$APP/public/icons"
FRAME=$(magick identify -format '%w %s\n' "$SOURCE" | sort -nr | head -n 1 | awk '{print $2}')
mkdir -p "$OUTPUT"
for size in 192 512; do
  magick "${SOURCE}[$FRAME]" -resize "${size}x${size}" -strip "PNG32:$OUTPUT/aniviewer-$size.png"
done
magick "${SOURCE}[$FRAME]" -resize 180x180 -background '#2b2e32' -alpha remove -alpha off -strip \
  "PNG24:$OUTPUT/apple-touch-icon.png"
# The whole artwork square fits inside Android's central 40%-radius safe circle.
magick "${SOURCE}[$FRAME]" -resize 288x288 -background '#2b2e32' -gravity center -extent 512x512 \
  -alpha remove -alpha off -strip "PNG24:$OUTPUT/aniviewer-maskable-512.png"
