# Web Costume Compatibility

The web renderer now uses the desktop's costume sheet routing, replacement-clone
policy, overlay ordering, draw-only pivot correction and attachment alignment
rules. This is tested compatibility for these paths, not a claim that every
costume or device is visually identical to the desktop or game.

## Changes

- Route each remap to its named sheet, with sheet aliases as a fallback. Do not
  select a sprite from an arbitrary costume atlas just because its name matches.
- Reuse embedded canonical clones. Support legacy reversed clone labels and
  source anchors. Hide unused canonical clones; retain the original layer for
  single replacements without collapsing deliberately stacked replacements.
- Place shade-derived overlays above their reference body, force authored opaque
  replacements to full opacity, and retain explicit blend overrides.
- Parse absent atlas pivots as 0.5 and absent original dimensions as sprite size.
  Preserve DOF pivot-mode metadata.
- Keep costume pivot corrections in drawing, not the world transform inherited
  by descendants. Mixed-pivot frame swaps share the primary resource anchor when
  required by the desktop rule. Generic remapped pivots use the desktop's active
  `costume_pivot_adjustment_enabled=False` branch, not the older optional formula
  described in its comment. No monster-specific offsets are introduced.
- Align same-sheet, same-canvas attachments by canvas origin; otherwise use the
  rendered anchor including pivot and trim. An explicit root bypasses automatic
  canvas-mode selection.
- Use the desktop shader preset geometry for sheet-projected overlays, including
  authored UV offsets/divisors and packed corner ordering. Unrotated strip shaders
  retain the desktop's equal-slot mapping.
- Initialize layer-panel visibility from the applied costume as well as the
  renderer, so the first visibility toggle has the expected effect.

## Verification

Unit tests cover replacement vs stacked clones, canonical reuse, missing
references, overlay blend overrides, sheet aliases, mixed pivots, hires, DOF
exclusions, unchanged child transforms, and attachment alignment.

Browser tests check actual pixel output and viewport geometry for frame swaps,
atlas collisions, and sheet-projected shader frames, including exports. They run
at desktop, mobile-emulated and 320px widths. These are Chromium tests, not
physical iOS/Android certification.

An optional local corpus test compares web results directly with methods extracted
from the desktop implementation, without launching Qt or requiring proprietary
assets in the repository:

```sh
ANIVIEWER_COSTUME_CORPUS="/path/to/My Singing Monsters/data" \
  npx vitest run src/core/costume-corpus.parity.test.ts
```

The current corpus covers A costumes 07/09, Z11 costume 03 and DEN_RARE costume 01.
For every track, it compares layer order, parents, anchors, visibility, sprite
remaps, opacity and mixed-pivot anchor maps. It does not compare screenshots from
the game, every shader, or every attachment animation. It requires Python's
standard library and the local game data; ordinary tests do not.
