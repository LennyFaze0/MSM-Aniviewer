# Tool Mode Popout

Updated 2026-09-15. The former triple-tap/hold Cover Flow picker is replaced by a
button-driven, Launchpad-like static grid. This document retains its filename so
existing links still resolve. Transform mathematics, keyframes, edit links, IK,
and export serialization are unchanged.

## Interaction

1. Press **Tools** in the bottom viewport editing strip. The button also displays
   the current mode. The grid expands from that button.
2. All five modes are visible together: Select, Move, Rotate, Scale, Pan. Each has
   an icon, large text, and a short description. The current mode is highlighted.
3. Tap/click one tile to activate its mode. The grid shrinks back into Tools.
4. Cancel, Escape, or tapping outside dismisses it without changing the mode.

No tap sequence, hold timer, swiping, or release-to-choose gesture is required.
Touch-first devices (`pointer: coarse` and `hover: none`) retain one compact row:
Tools, Undo, Redo, Multi, Snap, Clear. The selected mode's precision buttons remain
available below that row. Desktop keeps its existing mode shortcuts as well.
The Sprites panel retains its native selector on touch-first devices.

## Accessibility And Safety

- Tools reports its current mode, expanded state, and associated dialog.
- The active tile gets initial focus. Tab/Shift+Tab cycle within the choices;
  arrows navigate the grid, Home/End reach its ends, and Enter/Space activate.
- Viewer shortcuts cannot manipulate sprites behind the open dialog. Closing
  restores focus to Tools when it is still available.
- Focused toolbar buttons retain native Space/Enter activation instead of
  triggering global playback. A touch's follow-up click is consumed even when
  WebKit reports it as a mouse click; a fresh pointer press is not consumed.
- An outside press/release is consumed by the backdrop, not passed to the canvas.
  A mode tile accepts only a pointer click whose press began on that tile, or
  keyboard activation. This prevents the launcher's mobile compatibility click
  from selecting a tile as the popup animates into place.
- Opening cancels an in-progress manipulation/IK placement. Choosing a tool does
  not write edits, keyframes, links, history entries, or sprite matrices.
- Resize, window blur, hiding the page, document/track changes, and export locking
  use shared cancellation and close the popout rather than leaving stale state.
- Normal touch taps tolerate eight client CSS pixels of jitter. Larger drags
  enter normal manipulation. Two fingers still exclusively pan/zoom the camera.

The dialog sits in the browser top layer, outside the viewport's clipped/zoomed
container. It is anchored above Tools and clamped to the visual viewport. It can
temporarily cover other controls on a short phone screen, rather than compress
its tiles into illegible buttons. Theme colors and application UI scale apply.
Opening takes 220ms and closing 160ms; reduced-motion preferences skip animation.

## Implementation

- `web-app/src/core/manipulation-modes.ts`: shared mode list and labels.
- `web-app/src/app/tool-mode-picker.ts` and `.css`: static grid, focus,
  opening/closing animation, and visual-viewport placement.
- `web-app/src/app/sprite-transform-controls.ts`: Tools button, ordinary pointer
  handling, cancellation, tool activation, and inspector fallback.

Native [dialog](https://developer.mozilla.org/en-US/docs/Web/HTML/Reference/Elements/dialog)
provides modal input isolation. Animation uses
[Element.animate](https://developer.mozilla.org/en-US/docs/Web/API/Element/animate).
There is no platform-specific OS widget or external UI dependency.

## Verification

Final 2026-09-15 checks: type-check and production build passed; 184 unit tests
passed (10 optional-fixture skips). The final focused picker/sprite/link/IK runs
passed 63 Chromium checks (9 platform/fixture skips) and 41 WebKit checks
(7 platform/fixture skips). Portrait/landscape screenshots were inspected.
The complete Chromium suite also passed 212 checks (22 skips) before the final
touch-click deduplication and toolbar keyboard activation adjustments; those
adjustments were verified by the final focused runs. Existing dependency/bundle
packaging warnings remain in the production build.

`tests/tool-mode-picker.spec.ts` covers mode activation, static labels, exported
pixel/selection/history invariance, outside dismissal, focus return, keyboard
containment, reduced motion, cancellation, native selector, and 320px portrait /
short landscape / 125% UI-scale layouts. Native browser taps are used on both
mobile Chromium and mobile WebKit, with clicks on desktop. Triple taps no longer
open tools. Tap, fresh mouse click, and Space each produce one precision step,
then undo one at a time. The existing sprite/link/IK suites cover manipulation, undo/redo,
camera takeover, exports and source-keyframe isolation.

These are automated browser checks, not physical iPhone/iPad/Android validation.
Optional real-asset fixture tests require their configured local inputs.

Run from `aniviewer/web-app` with its development server available:

```bash
./node_modules/.bin/vitest run src
./node_modules/.bin/playwright test tests/tool-mode-picker.spec.ts \
  tests/sprite-transform.spec.ts tests/edit-constraints.spec.ts tests/ik-constraints.spec.ts
./node_modules/.bin/playwright test --config=playwright.webkit.config.ts \
  tests/tool-mode-picker.spec.ts tests/sprite-transform.spec.ts \
  tests/edit-constraints.spec.ts tests/ik-constraints.spec.ts
./node_modules/.bin/tsc --noEmit
./node_modules/.bin/vite build
```

The WebKit configuration supports `ANIVIEWER_WEBKIT_EXECUTABLE` for a compatible
local launcher on unsupported Linux distributions. Temporary OS compatibility
libraries and this launcher do not belong in the viewer repository.
