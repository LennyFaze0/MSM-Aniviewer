# Aniviewer 3DS

This is a 3DS homebrew playback-only alpha for Rev6 animations.

## Build requirements

- devkitPro
- libctru
- citro3d
- citro2d

## Build

```bash
cd viewer
make
```

Output:
- `build/aniviewer3ds.3dsx`
- `build/aniviewer3ds.smdh`
- `build/aniviewer3ds.elf`

## Runtime data paths

The app discovers raw Rev6 BINs from:

 `sdmc:/3ds/aniviewer3ds/raw/*.bin`

### Raw Rev6 BIN requirements

For textured raw BIN playback, the BIN should reference atlas XML sources that are present on SD, and each atlas should have a matching `.t3x` sheet available. The loader resolves atlas and `.t3x` paths relative to the BIN and atlas directories.

If `.t3x` sheets are missing or fail to load, playback still runs in debug-quad fallback mode.

## Current rendering status

- Loads animation metadata, layers, and keyframes
- Plays timeline with interpolation
- Applies hierarchical parent/child transform composition for layer accuracy
- Renders textured sprites from external `.t3x` sheets resolved from raw BIN atlas XMLs
- Falls back to animated debug quads when textures are unavailable

## Controls

 `A`: pause/resume
 `X`: restart timeline
 `Y`: reload current BIN
 `SELECT`: cycle animation within current BIN (when available)
 `DPad Left/Right`: switch BIN
 `DPad Up/Down`: speed
 `L/R`: zoom
 `3D slider`: depth strength
 `Circle Pad`: depth tilt
 `START`: exit

If you need a fresh rebuild, run `make clean && make`.
