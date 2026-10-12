# Web Audio Playback

## Problem And Evidence

2026-10-01: the user reports mobile/iOS audio cutting out before a monster's
synchronized track finishes. The exact iOS version, affected clip and cutoff
timestamp are not yet available. Do not claim a physical-device root cause from
desktop WebKit emulation alone.

The previous implementation always used an HTMLAudioElement with a blob URL.
An unexpected pause marked playback failed, returning the animation to wall time
until a new user action. Its tests verified element clocks/call counts, not output
samples. Async asset lookup could also outlive the first Play gesture, losing the
opportunity to unlock audio before automatic playback.

WebKit has a historical, resolved report of
[blob audio ending early](https://bugs.webkit.org/show_bug.cgi?id=238113).
That documents a class of failure, not proof that the same old bug affects the
user's current device. A more suitable primary path for bounded rhythmic clips
is [AudioBufferSourceNode](https://developer.mozilla.org/en-US/docs/Web/API/AudioBufferSourceNode):
decode the whole clip and reuse that buffer instead of driving media seeks.

## Transport

- `web-app/src/core/audio-playback.ts`: facade, shared context authorization,
  bounded decoding, serialization of decode tasks, generation cancellation,
  backend choice, unchanged source Blob for exports.
- `web-app/src/core/buffered-audio-playback.ts`: context-clock transport, gain,
  pause/seek, matching native loops, interruption/resume and source cleanup.
- `web-app/src/core/media-audio-playback.ts`: retained media fallback, including
  native matching loops, silent tails and seek-completion resume handling.
- `web-app/src/main.ts`: hold the renderer while audio is preparing; preserve
  current transport intent through async loading; report backend in
  `#viewportPanel.dataset.audioBackend` (`loading`, `buffer`, `media`, `none`).

No UA sniffing: capable desktop and mobile browsers use the same transport.
The context is shared across track changes and unlocked synchronously from a
Play/Unmute gesture, even if asset lookup has not finished. Sources are replaced
only by transport actions, not render-clock corrections. Matching cycles loop on
one buffer source, with no duration argument that could truncate it.

Context time stops when the OS suspends/interrupts it, so the animation holds too.
The status reports the context state. A fresh gesture retries resume; visibility
return makes one attempt only when playback is wanted. It is not an every-frame
retry loop. See [context states and iOS interruptions](https://developer.mozilla.org/en-US/docs/Web/API/BaseAudioContext/state).
Background playback/lock-screen continuity is not guaranteed, and a context
the OS refuses to resume still requires user intervention.

The encoded limit is 8 MiB, the retained PCM limit is 32 MiB, and metadata must
describe one or two channels. Mediabunny metadata preflight estimates PCM at the
context's sample rate before native decoding; actual output is checked again.
One decode runs at a time and stale results are discarded. These are admission
and retention limits, not a hard cap on transient browser decoder allocations.
Rejected/unsupported clips retain the media fallback, not silence or discarded
audio. No source asset is transcoded or changed on disk.

Muppets timing, standard/DOF timing, non-matching cycles and shorter clips' silent
tails retain their prior policy. In particular, a standard animation shorter than
its audio still uses its existing animation-cycle boundary; this change does not
reinterpret every game's loop structure. Exports decode the original Blob
independently, not from the live playback buffer or clock.

## Verification

- Existing media transport unit tests are retained in `audio-playback.test.ts`.
- `buffered-audio-playback.test.ts` covers full source playback, loop-off after
  multiple cycles, explicit scrubs, pause/mute, interrupted context clocks,
  pending permission retries, early Play, silent tails, and destruction.
- `audio-playback-routing.test.ts` covers memory admission, unsupported decoding,
  missing APIs, retained settings/source Blob, early authorization, serialized
  decoding, rapid track replacement and stale-load cancellation.
- `tests/audio-playback.spec.ts` explicitly disables Web Audio to test fallback.
- `tests/buffered-audio-playback.spec.ts` uses real decoded WAVs and an analyser
  after the gain node. It verifies nonzero samples late in the first complete
  track and after multiple loops, one looping source, timeline agreement, mute,
  scrub, suspension/Unmute recovery, early Play during decoding and silent tails.

Validation: 205 unit tests passed (10 optional-fixture skips); typecheck
and production build passed. Focused audio browser suites passed 22 Chromium and
22 WebKit tests, across desktop and mobile profiles. The full Chromium regression
suite passed 227 tests (22 fixture/platform/optional-test skips), covering exports,
asset loading, transforms, constraints, and mobile controls as well as audio.
Browser tests cannot certify
physical iPhone output routing, codec support, calls, or lock-screen policy.

Three local game clips (`02-AB_Monster_01.ogg`, `01-BCDE_Monster_01.ogg`,
`01-BCE_Monster_02.ogg`) decoded through the buffered path in both engines.
Their complete durations matched ffprobe within one decoded sample; no game
assets were added to the repository. This codec check is not an assertion that
the user's particular failing clip was reproduced.

```bash
cd aniviewer/web-app
./node_modules/.bin/vitest run src
./node_modules/.bin/playwright test tests/audio-playback.spec.ts tests/buffered-audio-playback.spec.ts
./node_modules/.bin/playwright test --config=playwright.webkit.config.ts \
  tests/audio-playback.spec.ts tests/buffered-audio-playback.spec.ts
./node_modules/.bin/tsc --noEmit
./node_modules/.bin/vite build
```

Run a dev server on port 4173 first on machines without npm. Unsupported Linux
hosts can use the existing `ANIVIEWER_WEBKIT_EXECUTABLE` override with an external
compatibility launcher. Such libraries and scripts do not belong in the repo.
