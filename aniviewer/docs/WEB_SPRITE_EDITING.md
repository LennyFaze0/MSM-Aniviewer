# Web Sprite Manipulation

Implemented 2026-09-10; explicit edit links added 2026-09-11; analytic one/two-bone
IK added 2026-09-12. This is not a port of desktop keyframe recording, its legacy
joint solver, or editable project persistence.

## Controls

- The Tools button in the bottom viewport strip expands a static grid: Select,
  Move, Rotate, Scale, and Pan. Click/tap a mode to select it and shrink the menu
  back into the button. Cancel, Escape, or an outside tap dismisses it. The former
  triple-tap/hold gesture is removed. Desktop shortcut buttons remain available;
  mobile saves that row. Undo/Redo, Multi, Snap, Clear, and precision controls
  remain. Sprites also has a standard mode selector for accessibility.
  See [Tool Mode Popout](WEB_TOUCH_MODE_PICKER.md) for interaction and tests.
- Select by clicking/tapping a rendered sprite or its Layers row. Multi toggles
  additive selection on both surfaces; Ctrl/Cmd/Shift-click also works. Alt-click
  cycles overlapping sprite bounds. Hidden/invisible/missing sprites are not picked.
- Move drags in animation-world pixels, independent of camera zoom, DPI, UI scale,
  and viewport borders. Rotation uses its anchor handle or horizontal sprite drag.
  Scaling uses horizontal drag or the step controls, with Both/X/Y axis selection.
- Large direction buttons, 0.1/1/10px steps, rotation increments and scale steps
  provide touch alternatives to free dragging. Snap quantizes drag amounts to the
  move step, 15 degrees, or 5% scale. Shift temporarily enables snapping.
- Sprites tab: numeric X/Y offsets, rotation, X/Y scale percentages, flip and reset.
  Mixed multi-selection values are shown as Mixed. Positive Y is down; positive
  rotation is clockwise in the viewport. Negative scale mirrors; zero is bounded
  to 1% so a sprite cannot become unselectable through a singular user scale.
- Unlinked layers rotate/scale about their own authored anchors, including
  frame-specific anchor maps. Edit Links in the Sprites tab adds Move together,
  Choose what follows, and Off. Linked groups share the driver's anchor; group
  X/Y scaling uses world axes. Dashed outlines mark visible followers.
- Two fingers exclusively pan/zoom the camera. A second finger cancels a partial
  sprite drag; the remaining finger cannot resume that edit accidentally.
- Pan and middle-mouse drag move only the camera. Wheel zoom retains its existing
  cursor-anchor setting. Editing gestures pause playback; camera gestures do not.
- Undo/redo operate on complete gestures, including multi-selection. Escape,
  pointer cancellation, window blur, hiding the page, scrubbing, or exporting
  cancels a partial gesture. Cancel does not clear redo history.
- Keybind defaults: Q Select, V Move, E Rotate, S Scale, H Pan; arrow keys nudge,
  Shift+arrows nudge 10x. Configurable in Settings, including undo/redo. Existing
  saved blank bindings stay blank. Text fields and dialogs keep normal typing.

The explicit edit modes, free movement, and precision step controls take design
cues from the [Geometry Dash Editor Guide, Editing chapter](https://a-zalt.github.io/gdknowledge/assets/GJGuide_new.pdf).
The viewer retains its own desktop-style theme and does not reuse game art.

## Transform Contract

1. Evaluate original keyframes and their original hierarchy unchanged.
2. Apply the committed edit-operation stack to evaluated world matrices. Unlinked
   edits affect only their driver; explicit links share full or channel-filtered
   affine operations with their captured member scope.
3. Never feed that edited matrix back into animation-child hierarchy evaluation.
   Unlinked, unedited children/grandchildren keep their original matrix exactly, rather than
   relying on inverse compensation or iterative correction.
4. Evaluate explicit [IK chains](WEB_IK_CONSTRAINTS.md), if any, against the
   post-edit state. Disabled chains do not write transforms; active chains own
   only their specified bones/followers. No source reparenting is performed.
5. Pass these same edited states to viewport rendering and PNG/GIF/video exports.
   Costume attachments belonging to an edited owner follow that owner's edited
   matrix. Other animation layers receive user edits only through explicit links
   or direct selection.

Base animation still plays normally; this does not freeze every child across time.
Offsets affect the entire active animation, including exports. No source keyframes,
BIN/JSON assets, joint definitions, or source files are rewritten.

## Modules

- `web-app/src/core/layer-transform.ts`: pure affine edits, anchors, bounds hit tests.
- `web-app/src/core/edit-constraints.ts`: edit graph validation, channel planning,
  full affine operation evaluation, and rigid-member routing.
- `web-app/src/app/edit-constraint-controls.ts`: explicit driver/membership UI.
- `web-app/src/app/viewer-session.ts`: transient edits, previews and atomic history.
- `web-app/src/app/sprite-transform-controls.ts`: pointer/touch state machine,
  selection overlay, toolbar, numeric fields and commands.
- `web-app/src/core/manipulation-modes.ts`: shared mode names/order.
- `web-app/src/app/tool-mode-picker.ts`: themed static grid, animation and focus handling.
- `web-app/src/renderer/animation-renderer.ts`: shared edited render states,
  camera methods and rendered-geometry queries. Editor decorations are DOM SVG,
  separate from the exported Pixi scene.
- `web-app/src/main.ts`: integration, list selection, settings shortcuts, export lock.

## Current Limits

- Edits reset on animation/appearance/file changes or browser refresh. Retained
  assets are not an editable-project save. The UI says this explicitly.
- Picking uses transformed sprite bounds, not per-pixel transparency. Use the
  Layers panel or Alt-click for overlapping/transparent rectangular regions.
- No animation-key writing, per-key influence, desktop joint-solver parity, source-anchor
  authoring, attachment sublayer editing, box selection, or keyframe propagation yet.
- Scale is limited to 1%-10000% in magnitude. Playback's original zero scales can
  still hide a source sprite; these are not rewritten by the editor.
- Browser touch automation is not physical iPhone/iPad/Android validation.

## Verification

`src/core/layer-transform.test.ts` covers sheared/non-uniform/reflected bases,
mapped anchors, independent parent/child/grandchild edits, angle-wrap and hit tests.
`src/app/viewer-session.test.ts` covers atomic history, cancellation, redo, track
isolation and snapshot ownership. `tests/sprite-transform.spec.ts` imports a real
three-generation fixture, checks exported colored pixels, numeric and pointer
edits, camera isolation, hidden layers, mobile multi-touch and compact layout.

Next stage: design editable-project persistence and keyframe serialization against
this single render contract, with before/after world-matrix AND exported-pixel
round-trip tests, before attempting full desktop recording parity.

See [Edit Constraints](WEB_EDIT_CONSTRAINTS.md) for the research, affine proof,
existing-keyframe policy, link-change semantics, and private-asset test commands.
