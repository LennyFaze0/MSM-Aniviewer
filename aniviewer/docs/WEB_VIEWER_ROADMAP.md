# Aniviewer Web Parity Roadmap

Aniviewer Web is the primary future edition of Aniviewer. The finished application
must provide the desktop viewer's functionality from a public GitHub Pages site,
without requiring users to install Python, Qt, FFmpeg, or a platform-specific build.

The Python desktop application remains the behavioral reference until every supported
workflow has a browser equivalent and passes shared fixture tests. Existing browser
parsers and renderers are retained when they match that reference; UI and editing
code may be replaced as needed to reach a maintainable architecture.

## Non-negotiable product requirements

- User-supplied assets remain on the user's device and are processed locally.
- The production application is static and deployable through GitHub Pages.
- Desktop, tablet, and mobile browsers expose the same project capabilities.
- Touch, pointer, keyboard, and accessible non-canvas controls are first-class inputs.
- Raw game folders, ordinary files, and ZIP archives remain valid inputs.
- Missing or unsupported dependencies are named and recoverable in the current session.
- Editing never mutates source files until the user explicitly exports a result.
- Desktop behavior is captured in fixtures before the desktop implementation is retired.

## Architecture contract

- `src/core/` owns format parsing, animation evaluation, and platform-neutral data.
- `src/renderer/` owns visual output and hit testing, not document mutation.
- `src/app/` owns session state, commands, undo/redo, selection, and editing policy.
- UI controllers render session state and dispatch commands; they do not become a
  second source of truth.
- Import, render, edit, record, and export use one canonical `AnimationDocument` model.
- Every editing command must be deterministic, undoable, and testable without a canvas.

## Stage 0: Browser foundation

- [x] Responsive desktop, tablet, and mobile workspace.
- [x] Local file, folder, drag-and-drop, and lazy ZIP indexing.
- [x] Revision 1-6 BIN parsing in a Web Worker, including Rev 1 launch/mobile,
  Muppets, and Monster Choir format variants.
- [x] Full-field desktop parity for representative Rev 1-6 files and a local
  2,451-file untouched serializer corpus gate.
- [x] Dedicated My Muppets Show parsing and direct-image sources.
- [x] TexturePacker XML plus PNG, AVIF, WebP, and JPEG sources.
- [x] WebGL playback, hierarchy transforms, blend modes, trim, rotation, and depth.
- [x] Main-game costumes, shader sequences, attachments, and layer colors.
- [x] DOF Unity bundles, compressed textures, chromatics, audio, and particles.
- [x] Static-site DOF asset grabber using user-directed CDN downloads.
- [x] File Selection Monster Browser with Rosetta names, local/ZIP portraits, search,
  source filters, and explicit selection between duplicate or variant animation files.
- [x] Root-level GitHub Pages build and deployment workflow.
- [ ] Clean Linux, Windows, and macOS development setup verification.
- [x] Opt-in IndexedDB asset copies and selection restore, saved read-only folder handles,
  permission-aware startup refresh, and cached-copy fallback when native access is unavailable.

## Stage 1: Shared application state

- [x] Central session owner for document, track, time, playback, selection, and visibility.
- [x] Bounded command history foundation with deterministic undo and redo.
- [x] Layer-panel single and additive selection on pointer and touch devices.
- [ ] Split file import, DOF grabber, appearance, timeline, and layer controllers out of
  `main.ts`.
- [ ] Add editable document transactions and dirty-state tracking.
- [x] Persist browser-native viewer preferences without persisting game assets by default.

## Stage 2: Viewport editing parity

- [x] Renderer bounds hit testing and viewport layer selection.
- [x] Pointer/touch translation with multi-selection, independent offsets and atomic undo.
- [ ] Persistent selection/layer locking.
- [x] Rotation and uniform or per-axis scale controls, including touch step buttons.
- [ ] Numeric position, rotation, scale, opacity, depth, and tint controls.
  Position/rotation/scale, flip and reset are available; opacity/depth/tint remain pending.
- [ ] Layer visibility, ordering, source selection, and attachment controls.
- [ ] Offset presets and non-destructive reset behavior.
- [ ] Undo and redo for every document mutation.
  Transform gestures support undo/redo, but are not source-document mutations yet.

See [the web manipulation contract](WEB_SPRITE_EDITING.md) for ownership,
non-inheritance, touch behavior, session-only limits and validation.

## Stage 3: Timeline and keyframe parity

- [ ] Full and Simple Keyframe Mode timelines.
- [ ] Keyframe markers, lanes, selection, dragging, deletion, and interpolation controls.
- [ ] Record Pose serializes the exact visible viewport pose.
- [ ] Current-keyframe and Propagate Forward influence modes.
- [ ] Joint Solver and constraints with explicit, tested enabled/disabled behavior.
  Animation-wide edit links are implemented separately: Move together, selective
  following, and Off. They preserve source keys; recording/solver parity is still
  pending. [Analytic one/two-bone IK](WEB_IK_CONSTRAINTS.md) is now available as
  a separate session-only target solver. It does not implement record/propagation.
  See [edit constraints](WEB_EDIT_CONSTRAINTS.md).
- [ ] Deterministic parent, child, and grandchild recording fixtures.
- [ ] BPM, metronome, audio synchronization, and frame stepping parity.

## Stage 4: Specialized viewer tools

- [ ] Costume selection and conversion workflows.
- [ ] Sprite Workshop and atlas source switching.
- [ ] Island, terrain, and structure rendering controls.
- [ ] MIDI editor and multi-view workflows.
- [ ] Complete focus mode, diagnostics, keybinds, theming, and UI scaling parity.
  The top settings drawer, persistent themes/UI scale, supported panel shortcuts,
  renderer sampling, particle limits, playback defaults, and export defaults are in place.
- [ ] Stereoscopic and accessible fallback viewing modes where browser APIs permit them.

## Stage 5: Export and migration parity

- [x] Transparent or solid-background PNG and stable-bounds frame-sequence ZIP export.
- [ ] Sprite-layer PSD export.
- [x] GIF and browser-compatible MP4/MOV/WebM video export, with worker and compatibility paths.
  Transparent video, ProRes and exact desktop presets remain pending; see [export coverage](WEB_EXPORT_PARITY.md).
- [ ] After Effects rig export.
- [ ] JSON serialization and byte-verified BIN writers for supported revisions.
- [ ] Layer-pass assignments and batch animation export.
- [ ] Monster migration as ordinary files or ZIP downloads.
- [ ] Browser capability messages for formats that cannot be reproduced exactly.

## Stage 6: Fidelity and public-release gates

- [ ] Side-by-side desktop and browser render captures for representative fixtures.
- [ ] Required real-game corpus tests run in release CI rather than silently skipping.
- [ ] Large Rev 6, costume, and DOF memory/performance budgets.
- [ ] Texture eviction and cancellation for low-memory mobile devices.
- [ ] Keyboard, screen-reader, reduced-motion, and touch accessibility passes.
- [ ] Chromium, Firefox, and WebKit support matrix.
- [ ] Installable offline application manifest and offline shell verification.
- [ ] Desktop application retirement checklist and migration documentation.

## Definition of parity

A feature is not complete because its control exists. It is complete when the same
input assets and user actions produce equivalent visible output and exported data on
desktop and mobile, its mutations round-trip through undo and redo, and automated
fixtures protect the behavior from regression.
