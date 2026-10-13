# Web Animation Color Compatibility

## Cause And Fix

Monster recolors use authored RGB values and compositing, not a monster-name hue
lookup table. The web viewer already evaluated Rev 6 RGB keys, but interpreted raw
blend ID 1 as normal transparency in every file.

The desktop applies `_determine_blend_version` before `_normalize_blend_value`:

- Files using only IDs 0 and 1 use the legacy mapping, even when tagged version 2.
- Legacy ID 1 is additive; modern ID 1 remains premultiplied alpha.
- A version 2+ tag is honored only when at least one layer in any track uses ID 2+.
- Modern IDs in untagged/hybrid documents are preserved.
- Standard `Sprite_light*` layers using no shader or `Anim2D/Normal+Alpha` are
  promoted to additive, matching the desktop DOF compatibility rule.

`web-app/src/core/animation-blend.ts` ports those decisions. The renderer resolves
the file-wide mapping once on load, including inactive tracks, rather than changing
interpretation when switching animations. Costume modifications retain the base
file's interpretation. Attachments carry their source blend-version metadata and
fall back to the base mapping, matching the desktop attachment path. Particle
prefab blend IDs are already canonical and are not remapped.

Separately, Rev 4/5 parsers retain layer-level RGB, but the web evaluator previously
ignored it. It now uses that static color when no active RGB keys exist. Animated
keys replace the fallback rather than double-multiplying it. Replacement costume
art keeps its existing neutral-color behavior.

No parent color/opacity inheritance, hardcoded monster palettes, or changes to
texture premultiplication were introduced. Existing mode 6/7 Pixi compositing
approximations are outside this change; this is not a claim of full renderer parity.

## Verification

Rare Ghazt (`monster_g_RARE.bin`, Rev 6) uses RGB 246/253/41 on its head with raw
blend 1 and 20% opacity. The first `01-G_Dance_01` track has 24 layers affected by
the missing additive mapping. Before/after browser renders used the same BIN, XML,
AVIF, pose, and background; only blend interpretation changed.

A local comparison against the actual desktop policy methods extracted with Python
AST found zero blend-policy mismatches across 1,109 monster BINs (295,562 layer
instances):

| Local Asset Family | Parsed Files | Raw-1 Layers Restored To Additive |
| --- | ---: | ---: |
| MSM 5.3.0 Rev 6 | 762 | 7,984 |
| Monster Choir Android 1.3.0 | 274 | 1,234 |
| Monster Choir iOS 1.3.0 | 73 | 561 |

There were no parse failures in that comparison. These counts describe policy
parity, not pixel-by-pixel validation of every monster. No game assets are included
in the tests or repository changes.

From `aniviewer/web-app`:

```sh
npm test
ANIVIEWER_DESKTOP_BLEND_PARITY=1 npm exec vitest run src/core/animation-blend.parity.test.ts
npm exec playwright test tests/animation-color.spec.ts
```

The opt-in parity test reads the desktop methods directly without importing Qt.
Browser tests check actual viewport and export pixels for legacy tinted glows,
modern transparency with blend metadata on another track, and static layer colors
at desktop, mobile, and narrow viewport sizes. These are Chromium tests, not a
claim of physical iOS/Android device validation.
