# MSM Aniviewer Web

This is the browser-native successor to the desktop Aniviewer. It is a Vite,
TypeScript, and PixiJS application deployed beneath `/MSM-Aniviewer/` on GitHub
Pages. It accepts user-supplied raw assets and does not upload them to a server.

**[Open the web viewer](https://lennyfaze0.github.io/MSM-Aniviewer/)**

For release steps and live-site verification, see the
[GitHub Pages deployment guide](../docs/WEB_DEPLOYMENT.md). This is a static Vite
build, not a Jekyll site. Existing local-preview asset caches are separate from
the public website; import your assets once at the public address.

Adding the viewer to an iOS or Android home screen uses the viewer's existing
program icon. iOS receives a 180px touch icon; Android receives 192px/512px icons
and a padded maskable variant. All are derived from `build_assets/aniviewer program
icon.ico`, not separate artwork. Maintainers can regenerate them with
`bash scripts/generate-home-icons.sh` (ImageMagick required). Existing home-screen
shortcuts may need to be removed and added again to pick up the new icon.

## Local development

Node.js 24 LTS with npm 11 or newer is recommended. Dependencies must be installed
separately on each operating system because the build toolchain contains native
platform packages.

```powershell
cd web-app
npm ci
npm run dev
```

The production checks are:

```powershell
npm test
npm run test:e2e
npm run build
```

When Node is unavailable but `dist` has already been built, serve the production
preview with Python:

```powershell
python scripts/serve_preview.py
```

Open `http://127.0.0.1:4173/MSM-Aniviewer/`.

The current viewer loads native revision 1-6 animation BINs, dedicated My Muppets
Show Rev 2 BINs, or converted animation JSON alongside referenced TexturePacker XML
and image files. Revision detection runs in a Web Worker and can be overridden from
the BIN revision menu when needed. Muppets placeholder animations that reference a
single image directly are supported without requiring a generated XML atlas.

Raw DOF Unity bundles (`.unity3d`, `.bundle`, and `__data`) are extracted locally in
the browser. The viewer supports PVRTC and ASTC sheets, split alpha masks, authored
particle textures and motion, adult and baby track groups, and selected chromatic
bundles as appearance choices. Select related `staticinfo`, chromatic,
`static_data.unity3d`, `sounds.unity3d`, and `particles.unity3d` files together, or
select their parent folder. Extracted shared particle `particles/.../__data` files
are also accepted. The DOF grabber automatically includes `particles.unity3d` with
selected monsters and `static_data.unity3d` when a chromatic appearance needs its
authored gradient LUT.

The **DOF** button opens the Dawn of Fire Asset Grabber. Known iOS and Android build
manifests are included as small metadata snapshots, with filtering, visible-item
selection, individual downloads, and checked downloads. Bundle links point directly
to the original CDN; no game bundle is stored by or relayed through GitHub Pages.
On Chromium desktop browsers, **Download + import** can watch a user-authorized
Downloads folder and index completed bundles immediately, including files renamed by
the browser because an older download already exists. Other browsers retain the
**Open files** import path.
For another build, enter its build key and import that build's `bundles.json`.
The CDN does not currently grant cross-origin read access, so a static GitHub Pages
site cannot inspect an unknown remote manifest without that local manifest import.

Main-game costume BINs are discovered alongside the loaded monster. Their costume
XML and image sheets are resolved from the same selection and applied as appearance
choices, including sprite remaps, cloned layers, blend overrides, static or animated
per-segment colors, and independently timed animated attachments. Costume shader
presets support frame-sequence replacement textures and color-wave effects.

Audio can come from ordinary extracted OGG, WAV, MP3, M4A, AAC, or FLAC files, or
from a selected DOF sound bundle. Matching audio follows track selection and stays
synchronized with playback and timeline scrubbing.

Audio is the master playback clock. Bounded-size clips are fully decoded into a
Web Audio buffer before playback, avoiding blob-media streaming, seek completion,
and repeated media `play()` requests at matching loop boundaries. The animation
follows the audio context; startup/decode delays and OS audio-context suspension
hold animation time rather than letting it run silently ahead. Play authorizes
audio directly in the tap handler, including when file lookup/decoding is pending.
Unmute or a fresh Play action can resume an interrupted context; returning to a
visible page also makes one resume attempt if playback is still wanted.

Encoded files over 8 MiB, estimated decoded PCM over 32 MiB, more than two channels,
unknown metadata, or unsupported Web Audio decoding retain the media-element
fallback. Metadata is checked before decoding and PCM size checked afterwards;
the browser decoder's transient allocation is not a hard memory guarantee.
The fallback preserves its native matching loops and completion-bound seek resume.
Neither backend adjusts audio speed or seeks on ordinary frame updates. Existing
Muppets timing, other formats' cycle rules, silent tails, and export audio remain
unchanged. This does not promise background/lock-screen playback or add codecs
unsupported by the browser.

Regression checks cover real output samples late in a track and after loops,
pause/scrub/mute, delayed loading, interruption, permission recovery, stale-track
cancellation, and both backends. See [audio playback notes](../docs/WEB_AUDIO_PLAYBACK.md)
for the architecture and remaining physical-iOS validation.

The Export panel can save the current composed viewport frame as PNG or package a
complete PNG frame sequence as a ZIP. Exports use animation-native coordinates,
support transparent or solid backgrounds and 1x through 8x resolution, and can either
retain the animation canvas or calculate stable content bounds across every frame.
Sequence timing follows the active playback-rate correction and restores the exact
playback position after rendering.

### Video and GIF Export

The Export tab also offers MP4 (H.264/AAC), MOV (opaque H.264/AAC), WebM (VP9 or
VP8/Opus), and worker-encoded GIF. Auto selects an available video/audio codec pair; it never silently
drops requested audio to obtain a different format. Unsupported codecs produce an
actionable error. Fast WebCodecs encoding requires HTTPS or localhost. Auto can
fall back to real-time MediaRecorder when supported, including plain HTTP LAN
testing. Compatibility mode takes at least the animation duration, may skip frames
on slower devices, and requires keeping the tab visible. MOV needs fast mode.

Frames come from the same deterministic Pixi renderer as PNG exports, but go
directly into transferable VideoFrames instead of being compressed to PNG first.
A dedicated worker handles native encoding and muxing, with at most two transferred
frames outstanding and native encoder backpressure. Hardware acceleration is
requested where supported, not guaranteed; browser codecs manage their own threads.
This needs neither a server nor SharedArrayBuffer/cross-origin isolation headers,
so it can run on GitHub Pages. Rendering/GPU readback still happens on the main
thread. No claim is made that it is faster than desktop FFmpeg on every machine.

Frame settings control resolution, frame rate, stable bounds, and background.
Video is currently opaque: without solid fill, the background is black. Odd
dimensions are padded on the right/bottom, not stretched. Matched audio is decoded
independently of playback/mute; idle animations without a matched clip stay silent.
Muppets clips spanning multiple animation loops export the complete synchronized
cycle. Short clips leave a silent tail, and long clips are trimmed to the planned
video duration. Audio codec packet padding can add a few milliseconds to a file.

Cancel terminates the worker and restores the viewport. Encoded files remain in
memory until download, with a 128 MiB output budget and separate stereo/128 MiB
decoded-audio limit. Large audio files must still be decoded before their PCM size
can be checked. Progress reports actual file size and elapsed time. Desktop
FFmpeg presets in Settings are reserved; use the **Fast Video** settings instead.

Completed exports remain available in an Export Ready panel with Save File, Open
File, and Share where supported, so mobile users can save with a fresh tap even if
the automatic download is blocked. GIF has no audio and uses palette/1-bit alpha
with up to 256 colors and 50 fps. Transparent video, ProRes, PSD, AE, and batch
exports remain pending; see [export coverage](../docs/WEB_EXPORT_PARITY.md).

Mediabunny handles native encoding/muxing, its AAC extension supplies a software
fallback, and gifenc encodes GIFs. Notices and licenses ship in `public/licenses/`.

### Sprite Manipulation

Press **Tools** in the bottom viewport toolbar to expand a static grid of Select,
Move, Rotate, Scale, and Pan. Tap/click a tile to activate it and shrink the menu
back into the button. Cancel, Escape, or an outside tap dismisses it without
changing modes. No hold or triple-tap gesture is needed. Desktop shortcut buttons
remain available; touch-first devices use the compact Tools button instead of a
separate mode-button row. A standard selector also remains in Sprites.
See the [tool popout contract](../docs/WEB_TOUCH_MODE_PICKER.md).

Multi selects more than one sprite; step buttons and Snap provide precise touch editing. Two fingers
pan/pinch-zoom without changing sprites. Numeric offsets, scale percentages, flips,
and reset are in the Sprites tab. Undo/redo treats one drag as one action.

Edits run after the original animation hierarchy, so unlinked animation children
and grandchildren do not inherit them. In **Sprites > Edit Links**, choose a
driver and its following segments, then choose **Move together**, **Choose what
follows**, or **Off**. Following can be limited to movement, rotation, and/or scale.
Link changes preserve the pose and govern subsequent edits, not earlier ones.
Exports use the same edited pose. These are **session-only, animation-wide edits**,
not recorded keyframes; switching animation/appearance/files or refreshing resets
them. See [the manipulation contract and limits](../docs/WEB_SPRITE_EDITING.md).
The [constraint design and verification](../docs/WEB_EDIT_CONSTRAINTS.md) explains
original-keyframe safety, shared affine transforms, and opt-in real-asset tests.

**Sprites > IK / Limb Bending** adds one-bone aiming and two-bone limb targets.
Choose segments, place root/bend/tip joints in the viewport, optionally assign
following sprites, and create a chain. Drag the target or use numeric/nudge
controls. Targets can follow the original animation with an offset or stay pinned
in world space. Turning IK off removes the solve completely. Original keys remain
unchanged; exports include the solve. IK is session-only, not recorded keyframes.
See [IK setup, mathematics and limits](../docs/WEB_IK_CONSTRAINTS.md).

The Settings button opens a desktop-style pane from the top of the workspace. Browser
preferences are stored locally and include system/light/dark appearance, four-color
theme overrides, UI scale, viewport texture and particle behavior, playback and PNG
export defaults, manual or automatic BIN revision selection, diagnostics preferences,
and available keyboard shortcuts. Desktop-only settings remain identified as port
pending. The legacy BIN converter is intentionally excluded pending its browser-native
redesign.

Select related files together, select a parent folder, drag files into the viewport,
or open a standard ZIP. ZIP contents are indexed first and each animation, XML, or
texture is decompressed only when it is needed. Missing dependencies can be added to
the same browser session without reloading the page. All processing remains local.

### Remembering Assets

Enable **Remember imported assets on this device** in **Files > File Selection**
to retain the current library and future imports in IndexedDB. **Settings > Paths >
Manage retained assets** opens these controls. Retention is off by default.

- Saved data includes independent byte copies, original relative paths, and the last
  selected animation file/track. ZIPs stay compressed and are indexed lazily on restore.
- **Open Game Folder** uses a read-only native folder handle where supported. Once
  retention is enabled, the handle is saved too. Startup restores the cache first,
  then checks authorized folders for additions, removals, and size/modification-time
  changes. An incomplete scan never replaces a saved folder snapshot.
- Permission is never requested automatically on startup. If access has expired,
  cached copies remain usable and **Reconnect** requests permission from a user click.
- Browsers without the folder API, including Safari, use the cached-copy workflow.
  Reimporting files or a folder replaces same-path assets; it does not silently skip
  updated files. Cache copies do not monitor the original files in these browsers.
- Only supported asset types are retained, not game executables or unrelated files.
  Usage is shown in File Selection. Storage persistence is requested but not guaranteed;
  site-data clearing, private browsing, and browser quotas still apply. Libraries stay
  local to this browser/device/site and do not move automatically from localhost to
  GitHub Pages.
- Failed saves preserve the previous committed copy and expose **Retry saving library**.
  Turning retention off or choosing **Forget saved library** removes saved asset copies
  and game-folder handles, but leaves the current viewport and original files untouched.
  The Asset Grabber's separate Downloads-folder authorization is managed independently.

The **Monster Browser** button in **Files > File Selection** opens a searchable card
browser alongside the existing animation dropdown. It uses the shipped monster-name
Rosetta and any imported `monster_portrait_square_*` images (usually in `gfx/book`).
Missing portraits use a readable fallback; no artwork is downloaded. Filter MSM, DOF,
or other animation sources, then choose the exact file when several installations,
island variants, or BIN/JSON sources share an entry. Only one page of portraits is
decoded at a time, including when browsing a ZIP.

See [the web parity roadmap](../docs/WEB_VIEWER_ROADMAP.md) for the remaining path
to complete desktop and mobile editing/export parity. BIN format support and corpus
verification are documented in
[the parser compatibility guide](../docs/WEB_BIN_PARSER_COMPATIBILITY.md). Third-party components used
by the bundle extractor are documented in [the notices file](THIRD_PARTY_NOTICES.md).
