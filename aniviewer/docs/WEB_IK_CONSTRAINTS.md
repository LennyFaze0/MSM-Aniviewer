# Web IK Constraints

Implemented 2026-09-12. This is a new, independent analytic one/two-bone IK stage,
not the desktop record/compensation solver and not a copy of the Spine runtime.
It complements [Edit Links](WEB_EDIT_CONSTRAINTS.md). A rigid head assembly uses
edit links; a limb bending toward a hand/foot target uses IK.

## Workflow

1. In **Sprites > IK / Limb Bending**, open **Set up a new IK chain**.
2. Choose one segment (aim) or two segments (bend), then the actual sprite layers.
3. At a clear paused pose, press **Place root**, then click/tap the joint in the
   viewport. For two bones also **Place bend**. Finally **Place tip**. The bend
   click defines both the upper endpoint and lower start at the same world point.
4. Optionally choose extra sprites to follow each bone. Membership is explicit,
   including hidden sprites. Neither source descendants nor edit links are
   silently copied. Two bones/chains cannot own the same sprite, even when off.
5. **Create IK chain**. Nothing moves at creation. Drag its crosshair target,
   enter target coordinates, or use the 1px nudge buttons. Handles have 44px hit
   areas. The viewport Snap control also quantizes target drags to its move step.
6. Choose **Follow animation + offset** or **Pin in world space**. Switching space
   keeps the same requested target at the current frame. Target X/Y are offsets
   in the former mode, absolute animation-world pixels in the latter. Y goes down.
7. **Enable IK** off removes the solve completely. Undo restores the prior setup,
   target, enabled state, or removed chain. Reset target returns to original bend
   and a zero animation-relative offset, including when previously world-pinned.

Only the active chain has a target overlay; multiple disjoint chains solve at
once. Select them using Active chain. Change joints/membership by removing and
recreating a chain; removal is undoable. Scrubbing, normal edits, playback and
history changes invalidate unfinished joint placement rather than mixing poses.
Camera pan/zoom does not invalidate completed joints. Escape cancels placement or
a target drag. A second finger cancels target editing before camera manipulation.

## Evaluation Contract

The order is source keyframes/hierarchy -> normal edit operations -> explicit IK
-> sprite/attachment rendering. Viewport and image/video export use the same
evaluated state. The SVG editor guides are not exported.

Each bone stores two offsets relative to its sprite's authored anchor, plus
explicit follower IDs. Anchor-map sprite swaps are respected. Binding converts a
clicked world point to local coordinates once. A collapsed or near-singular bind
matrix is rejected with a request to choose another frame. Playback does not
invert matrices or repeatedly modify previous-frame state.

Each frame, the input is a fresh post-edit source pose. Bone lengths and original
bend come from this sampled pose, so original keyed translation, rotation, scale,
sprite swaps and animation timing are not frozen. Offset targets are relative to
the sampled tip; world targets remain fixed. The solver finds a new connected
chain, then maps each source bone and its followers to it with a rigid world
rotation/translation. Full source affine shape, including shear/reflection, is
preserved; no TRS approximation, stretch, compensation keys or residual nudging.
The solved matrices are never fed back through the original parent hierarchy.

**Off is a literal no-write branch.** A source-bend target exactly at the sampled
tip is also a no-write branch, so creating a chain or resetting the target does
not alter existing keyed poses, including intentional gaps. When actively solving,
IK enforces a connected chain; original gaps or independent limb rotations can
therefore change. This is a geometric constraint, not rigid edit inheritance.
Unassigned sprites keep their input state exactly. Owned costume attachments
follow the same owner matrix as ordinary renderer attachments.

## Mathematics and Limits

For root R, bone lengths L1/L2 and target T, clamp |T-R| to
`[abs(L1-L2), L1+L2]`. The law of cosines gives the upper angle; the second endpoint
is the clamped target and the bend chooses one of the two elbow solutions. Lengths
are normalized in the cosine calculation to reduce overflow. A one-bone chain
aims toward T at fixed sampled length. Equal-length fully folded chains retain
the sampled upper direction; a zero-length sampled bone skips IK and preserves
the input pose. There is no accumulation or iterative convergence threshold.

Unreachable targets show their residual in the UI, not a false success. Original
bend follows the sampled side and can flip if the source animation crosses
straight; explicit A/B holds the chosen world-space side. Source bend at an
numerically straight/folded pose (normalized cross magnitude <= 1e-9) uses the
chain's bind-side fallback instead of letting floating-point noise pick a side.
No stretch,
softness, partial mix, pole target, angle limits, collision/contact solver, or
chains longer than two bones in this implementation. One sprite cannot acquire
an anatomical knee through IK; that needs separate upper/lower art or deformation.

The implementation is session-only and animation-wide. Changing animation,
appearance, files, or refreshing clears IK and edits. It does not write source
keyframes, serialize projects, or port desktop Record Pose/Propagate Forward.
Edit Links Off remains prospective; IK Off instead removes the current solve.

## Research Basis

Spine documents one/two-bone target-based IK, bend direction, and optional
stretch/softness/mix. The target workflow informed this UI, not full feature
parity or reuse of its code. [Spine IK constraints](https://esotericsoftware.com/spine-ik-constraints).

The closed-form two-bone reach interval and two elbow solutions follow the
standard analytic derivation. Our world-matrix projection and animation-relative
target policy are viewer-specific design choices.
[Ryan Juckett: Analytic Two-Bone IK in 2D](https://www.ryanjuckett.com/analytic-two-bone-ik-in-2d/).

## Verification

- `src/core/ik-constraints.test.ts`: quadrants, lengths, both reach limits/bends,
  folded/singular inputs, anchor swaps, affine corner distances, explicit followers,
  unrelated-state identity, Pixi transform reconstruction and disjoint validation.
- `src/app/ik-session.test.ts`: repeated previews, single-command history, cancel,
  redo, disabled state, snapshot ownership, rejection and track reset.
- `tests/ik-constraints.spec.ts`: actual imported colored sprites, setup/no bind
  jump, target drag/nudge, pinned mode, PNG pixels, exact Off restoration at three
  animation times, mobile taps/multitouch, cancellation and ownership errors.
- `src/core/ik-constraints.assets.test.ts`: optional private BIN/JSON corpus checks
  at keys, just before/after keys and midpoints. Includes reflected/scaled/rotated
  normal edits before IK. All other layers remain exact; source keys never mutate.
  These are explicit mathematical probe bones, not a claim of inferred anatomy.

Run private-asset checks with `ANIVIEWER_CONSTRAINT_ASSET=/path/to/monster.bin`
and `npx vitest run src/core/ik-constraints.assets.test.ts`. No private game assets
or asset-specific runtime branches are shipped. Browser automation is not physical
iPhone/iPad/Android hardware verification.

### Completed Validation (2026-09-12)

- TypeScript and production Vite build passed. Existing third-party Unity/WASM
  packaging and large-chunk warnings remain; no new dependency was added for IK.
- Unit suite: 184 passed, 10 optional-fixture tests skipped.
- Full Chromium suite: 193 passed, 20 platform/optional-fixture skips. Following
  the final straight-joint and layout polish, focused IK/edit-link/transform
  checks were rerun: 44 passed, 7 expected skips across desktop, phone and 320px.
- Focused WebKit IK/edit-link/transform suite: 28 passed, 6 expected skips.
  Final IK-only rerun: 8 passed, 2 Chromium-only touch-injection skips, using
  desktop Safari and iPhone profiles. Native multitouch cancellation was tested
  separately through Chromium's touch injection, not simulated mouse claims.
- Private `monster_ab.bin`: 13 tracks, 49,623 sampled solves; maximum joint
  separation `1.1718571004216928e-13` world pixels, maximum tip residual
  `8.987733679556355e-14`. Every Off/idle input state retained exact identity;
  the original source document remained byte-for-byte equal under JSON comparison.
- Desktop/mobile screenshots reviewed; no horizontal page overflow and 44px
  mobile checkbox/target hit areas. Physical devices remain untested.
