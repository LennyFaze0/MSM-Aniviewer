# Web Export Coverage

Updated 2026-09-09. This is a capability map, not a claim of full desktop parity.

| Desktop output | Web status | Limits |
| --- | --- | --- |
| Current frame PNG | Implemented | Native animation coordinates; transparent or solid background |
| PNG frame folder | Implemented as ZIP | Browser download rather than unrestricted filesystem writes |
| MP4 | Implemented | H.264/AAC; native worker encoding, AAC software fallback, or real-time recorder |
| WebM | Implemented | VP9/VP8 with Opus; availability depends on browser codecs |
| MOV | Implemented, partial | Opaque H.264/AAC QuickTime; not desktop ProRes, QTRLE, or alpha output |
| GIF | Implemented | Worker palette encoding; no audio; 1-bit alpha; up to 256 colors/50 fps; no dithering yet |
| Layered PSD | Pending | Needs per-layer image export and composition/blend parity |
| After Effects rig | Pending | Needs asset packaging, anchors, parenting, and transform/animation parity |
| Transparent video / ProRes | Pending | Must preserve alpha and use compatible encoders; never substitute an opaque file silently |
| Layer/segment export passes | Pending | Must preserve visibility, costume/source selection, and scene state per pass |
| Batch all animations | Pending | Requires cancellable track iteration and reliable audio/asset restoration |
| Animation BIN writing | Pending | Requires revision-specific writers, not simply renaming JSON |

## Processing Modes

- **Auto:** fast WebCodecs worker where available; switches to real-time recording if a compatible recorder exists and fast encoding is unavailable/fails.
- **Fast:** deterministic timestamps, two transferred frames outstanding, no PNG intermediates, native encoding/backpressure. Requires WebCodecs in a secure context. AAC uses a lazily loaded software encoder if the browser lacks it.
- **Compatibility:** MediaRecorder over a dedicated render canvas and optional audio destination. Does not record the screen or request camera/microphone permission. Works on HTTP if the browser provides these APIs. Takes at least the animation duration, can skip frames on slow hardware, and cancels if the tab is hidden. It is not a frame-exact replacement for offline encoding. MOV is not supported here.

PNG and GIF do not require WebCodecs. Unsupported video formats produce a visible
error with alternatives, never a mislabeled extension or silent dropped audio.
GIF's palette and delay precision inherently differ from full-color video.

## Mobile Saving

Every completed file remains in an **Export Ready** panel with Save File, Open
File, and Share / Save to Files where supported. This enables a fresh user gesture
after encoding. Blob URLs stay valid until Clear Result or a replacement export;
the old next-task URL revocation is not used for these exports. Output remains
local and is not uploaded.

## Validation

Tests exercise worker encoding, actual container headers and audio, timestamps,
PNG/video position parity, cancellation, codec failures, missing APIs, blocked
automatic downloads, user-gesture sharing, and a real non-secure HTTP origin.
Chromium covers desktop, touch/mobile, and narrow layouts. Linux WebKit covers
native file generation and API-unavailable handling, but does not provide
MediaRecorder in this test build. Its VP9 decoder also rejects some files that
FFmpeg decodes correctly, so pixel parity is checked in Chromium and generated
WebKit files are additionally inspected with FFmpeg. None of these tests is a
claim of physical iPhone verification.

Vite prebundles the lazy/worker encoders at startup to avoid a dependency-discovery
reload during the first export. Production assets remain code-split.

## Next Parity Stages

1. Layer/segment image passes with composition tests, then PSD using those passes.
2. After Effects rig serialization against the desktop writer and reference assets.
3. Alpha-capable video/ProRes, with explicit per-browser codec strategy.
4. Batch-all-animations and revision-specific BIN writers with round-trip tests.
