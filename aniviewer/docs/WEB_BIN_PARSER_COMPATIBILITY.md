# Web BIN Parser Compatibility

The browser parser runs entirely in a Web Worker and produces the same canonical
`AnimationDocument` used by JSON, DOF, costume, rendering, and future export paths.
No user animation data is uploaded.

## Supported formats

| Viewer label | Parser format | Serialized revision | Notes |
| --- | --- | ---: | --- |
| Rev 1 | `rev1_launch` | 1 | Launch/classic mobile layout with sparse sprite changes. |
| Rev 1 (Legacy Mobile) | `legacy` | 1 | Later runtime-structure dump with optional frame payloads. |
| Rev 2 | `rev2` | 2 | Standard MSM layout, also used by My Singing Muppets beta. |
| Rev 2 (My Muppets Show) | `muppets` | 2 | Dedicated Muppets runtime layout and parent-ID mapping. |
| Rev 3 (Monster Choir) | `choir` | 3 | Choir-era aligned legacy layout. |
| Rev 4 | `rev4` | 4 | Adds per-frame mask and anchor data. |
| Rev 5 | `rev5` | 5 | Runtime dump with recoverable layer/animation boundaries. |
| Rev 6 | `rev6` | 6 | Current structured format, including embedded clone metadata. |

The game release number and serialized format are not always the same. For example,
the available iOS Monster Choir corpus uses the Rev 2 serializer, while its Android
sibling uses the Choir/Rev 3 serializer. Automatic mode reports the format actually
decoded rather than relabeling bytes from their containing release folder.

Source records are preserved even when they are blank or contain historical authoring
references such as `.avi` or After Effects composition names. Resolving a renderable
atlas is an asset-library responsibility and must not invalidate an otherwise valid BIN.

## Verification

The local compatibility pass currently covers:

- Complete source/animation tree parity against desktop-generated JSON for real
  `monster_a.bin` files from Rev 1 through Rev 6.
- 2,451 untouched monster animation files across the available Rev 1-6, Muppets,
  and Monster Choir release corpus.
- Synthetic byte-level fixtures for Rev 1 launch, Rev 1 legacy mobile, Rev 2 Muppets,
  unresolved source records, and Rev 6 embedded clone metadata.

Run focused parser tests:

```bash
cd aniviewer/web-app
npm run test:bin
```

Run the untouched corpus when it is available locally:

```bash
ANIVIEWER_REVISION_CORPUS="/path/to/revision tests untouched" \
  npm run test:bin:corpus
```

To enable complete Rev 1-6 desktop JSON comparisons, provide `rev1.bin` through
`rev6.bin` and their matching `rev1.json` through `rev6.json` in one directory:

```bash
ANIVIEWER_REVISION_PARITY="/path/to/revision-parity" npm run test:bin
```

## Remaining parser-adjacent work

- Add a real legacy-mobile corpus when representative files are available; current
  coverage for that variant is byte-level and follows the desktop reader exactly.
- Preserve and consume external Muppets context metadata where a viewer feature needs it.
- Apply embedded Rev 6 clone metadata through the web costume workflow; parsing now
  retains it, but renderer-side canonical clone behavior remains a later parity stage.
- Add deterministic BIN writers and byte-level round-trip tests before browser export
  claims parity with desktop conversion.
- Run the real corpus in release CI through a legally distributable fixture strategy.
