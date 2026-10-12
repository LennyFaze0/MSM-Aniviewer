# Web Viewer Edit Constraints

## Scope and contract

The web viewer has two independent transform systems. The first evaluates the
imported animation's existing keyframes and parent hierarchy. The second applies
explicit user-edit operations to the resulting world matrices. Edit links belong
only to the second system. They do not reparent source layers, modify interpolation,
insert compensation keys, or enable the desktop joint solver.

The intended result is an editable assembly whose original animation still plays.
A linked eyelid can blink and a mouth can change sprites while the entire head
assembly is moved, rotated, or scaled. This is different from freezing an assembly
at the moment a link is created. Existing relative animation is preserved; only
additional motion caused by the user's edit is shared.

The initial implementation is session-only and animation-wide. It is not a
keyframe-recording implementation, an IK solver, or a complete port of every
desktop constraint type. Changing animation, appearance, files, or refreshing the
browser clears edits and links. Retained assets are not an editable project save.
The separate [IK / Limb Bending stage](WEB_IK_CONSTRAINTS.md) now provides actual
one/two-bone target solving after edit links. Its Off setting removes the solve;
edit-link Off remains prospective as documented below.

## User-facing behavior

Edit Links is in the Sprites tab, below Selected Sprites. Choose a driver from the
list, or select a segment and use **Use selected as driver**. **Use animation
descendants** selects the driver's original children and grandchildren. The list
can be adjusted manually, including adding segments that are not original
descendants. **Apply links** commits the settings as an undoable action.

| Behavior | Driver edit | Following segments |
| --- | --- | --- |
| Off - edit independently | Only the driver receives new edits | No new edit inheritance |
| Move together - all transforms | Shared movement, rotation, and scale | Receive the same affine operation |
| Choose what follows | Driver receives the edit | Receive only enabled manipulation channels |

Move together makes member selection resolve to the group's driver. This prevents
accidentally dragging one eye out of an assembly that is supposed to remain glued.
The inspector identifies the driver being edited. Choose what follows allows a
member to be edited independently when it is selected on its own.

Solid outlines identify edited drivers. Dashed outlines identify visible
followers; hidden followers still participate and reappear in their edited
positions. Visibility is not a constraint switch. The full membership list is
available even when a segment has no currently visible sprite.

Link changes are prospective. Applying, disabling, removing, or changing a rule
does not change any existing edit operation. There is no bind-time jump and no
undoing an old edit merely because the checkbox is turned off. Off means that
subsequent edits no longer follow that rule. Undo is the way to reverse an earlier
edit; Reset selected removes operations authored by that driver, including their
recorded follower effects.

The following distinction is essential: **Follow movement** controls explicit
translation input. Rotation around the driver's anchor necessarily moves a
follower's position along an orbit, and group scaling necessarily changes its
distance from that anchor. Unchecking movement does not suppress those geometric
consequences of an enabled rotation or scale operation.

Both-axis scaling is uniform by default. Linked X/Y-only scaling uses world axes
around the driver's anchor. Independent, unlinked sprites retain the earlier
sprite-axis scaling behavior. This coordinate convention is stated in the UI; it
is not inferred by decomposing an animated parent matrix. The numeric inspector
shows authored input totals, not a decomposition of the resulting world matrix.
With interleaved non-uniform scales and rotations, those are not interchangeable.

## Research basis

Affine matrices provide a composable representation for translation, rotation,
scaling, reflection, and shear. PBRT describes this representation and notes that
a degenerate matrix cannot provide a usable inverse.[^1] The implication for this
viewer is that an animated zero scale must not make an unrelated member's edit
depend on inverting a collapsed driver.

Blender's Child Of documentation distinguishes direct matrix multiplication from
decomposition when all transform channels are enabled.[^2] This supports retaining
the complete affine result rather than trying to reconstruct a glued assembly
with independent per-sprite rotation/scale values. The viewer does not copy
Blender's binding UI or promise equivalent constraint-stack semantics.

OpenUSD explicitly orders transform operations and provides a separate control
for resetting inherited transforms.[^3] The relevant design principle is explicit
ownership and ordering. The viewer uses its own lightweight edit stack, not USD,
and does not reset or replace the imported animation's original inheritance.

These references support the mathematical foundation. Channel meanings, world-axis
group scaling, prospective link changes, and member-selection routing are viewer
design decisions, not requirements imposed by those sources.

## Affine evaluation

Let `B_i(t)` be a layer's world matrix after evaluating the unmodified source
animation at time `t`. For each edit operation, evaluate its driver in the current
intermediate state and find its authored anchor `p(t)`. Frame-specific sprite
anchor mappings use the same lookup as ordinary rendering.

For linked editing, construct the common delta:

```text
D(t) = T(p(t) + movement) * R(rotation) * S(scaleX, scaleY) * T(-p(t))
W_i'(t) = D(t) * W_i(t)
```

Scale is applied first, then rotation, then explicit movement. Every rigid member
receives the same `D`, not a similar transform around its own anchor. The driver
and all members retain their complete resulting 2D affine matrices. No inverse
parent decomposition, iterative nudge, residual acceptance threshold, or
post-record compensation pass is involved.

For any source-space point `q`, the required equality is:

```text
editedPoint_i = D * (W_i * q)
```

This also covers sheared or singular source matrices. When the driver matrix is
invertible, the usual relative-transform identity provides another explanation:

```text
inverse(D * W_driver) * (D * W_member)
    = inverse(W_driver) * W_member
```

Thus the added edit does not change the member's relative relationship to the
driver. That relationship can still change over time because of the original
animation. The implementation does not use this inverse identity to evaluate a
frame; point projection remains defined for collapsed animated transforms.

Selective following masks the explicit edit inputs before constructing a member's
delta. It does not decompose `D` and then guess which matrix entries represent a
position-only or scale-only edit. Rotation and scale inputs are identities when
disabled; movement is zero when disabled.

| Movement | Rotation | Scale | Additional follower operation |
| --- | --- | --- | --- |
| Off | Off | Off | None; original state object is retained |
| On | Off | Off | Translation |
| Off | On | Off | Rotation about driver anchor |
| Off | Off | On | Scale about driver anchor |
| On | On | Off | Rotation, then translation |
| On | Off | On | Scale, then translation |
| Off | On | On | Scale, then rotation |
| On | On | On | Complete shared operation |

## Graph and multi-selection

The edit-link graph has one incoming owner per member and one rule per driver.
Self-links, duplicate members, missing layers, conflicting owners, and cycles are
rejected before settings change. Disabled rules are validated too, so re-enabling
a rule cannot reveal a previously hidden cycle. Validation errors identify the
reason and leave the current pose and history untouched.

Nested groups are supported. A driver can itself be a member of another group.
Following channels are intersected along each path: a channel blocked at an
outer link cannot reappear just because an inner rule enables it. Original source
parentage is only a convenience for choosing members; it does not silently add
new edit edges.

For a multi-selection, drivers are planned in ancestor-first order. Each
layer/channel is claimed once. Selecting both a parent and a follower cannot
double the inherited movement. In selective mode, an explicitly selected child
may still receive its own rotation if the parent's rule blocks inherited
rotation. Rigid members instead resolve to the rigid driver before planning.

Flattening every descendant into one group can conflict with a separately owned
nested group. The UI reports the existing ownership rather than silently
reparenting or changing another rule. A nested structure should link the outer
driver to the inner driver and let the inner rule own its members.

## Existing keyframes and operation history

Each committed edit stores the driver, explicit transform delta, coordinate
convention, and resolved member/channel scope. Later rule changes cannot
reinterpret these records. At every frame, source animation evaluation starts
fresh, followed by the ordered edit operations. It never starts from the last
rendered frame. Repeated rendering, timeline scrubbing, and export therefore
cannot accumulate incremental corrections.

A gesture preview always uses the pre-gesture snapshot. Eighty pointer updates
produce one preview operation, not eighty accumulated operations. Commit creates
one undo command. Cancel restores the exact prior stack and does not destroy
redo. Undo and redo restore stored operations instead of applying inverse
floating-point corrections. Link changes use the same command history.

Removing an earlier operation changes the input to later operations, which is
normal ordered-stack behavior. Reset selected removes that driver's authored
operations; it is not a promise to freeze every later independently edited layer.
The previous complete state can be restored with Undo.

The existing history limit remains 200 commands. Older committed operations stay
in the visible edit stack even after they leave the undo window. Evaluation cost
grows with operation count and affected layer count. Long-session profiling and
safe stack compaction remain future work; arbitrary truncation would be a visual
data-loss bug.

## Renderer and export integration

`core/animation.ts` remains responsible for original keyframe and hierarchy
evaluation. `core/edit-constraints.ts` performs validation, driver routing, scope
planning, and affine projection. `app/viewer-session.ts` owns link settings, input
readouts, previews, committed operations, and undo/redo.

`app/edit-constraint-controls.ts` provides the membership and mode UI.
`app/sprite-transform-controls.ts` routes numeric, keyboard, mouse, and touch edits
through the session and displays driver/follower outlines.
`renderer/animation-renderer.ts` applies the operation stack before rendering
sprites and costume attachments. Viewport and PNG/GIF/video exports share that
evaluation path; editor outlines are separate DOM SVG and are not exported.

The renderer uses Pixi's affine transform support. A unit test verifies that the
actual installed Pixi transform representation reconstructs representative
sheared and reflected matrices. These checks protect against an unnoticed
render-layer loss of shear after the core math has already succeeded.

## Verification and limits

`core/edit-constraints.test.ts` covers all eight channel combinations, original
animation samples around swaps, frame-specific anchors, non-uniform and reflected
bases, collapsed animated scale, untouched unrelated layers, graph validation,
nested masks, and double-selected parents/members. An independent point-projection
oracle checks results rather than calling the same matrix writer twice.

`app/viewer-session.test.ts` covers no-jump link changes, disabled following,
hidden members, multi-selection, preview replacement, cancel, reset, immutable
snapshots, history, and track isolation. `tests/edit-constraints.spec.ts` exercises
the visible UI, actual exported colored pixels, touch and mouse drags, error
messages, and optional real-asset viewport geometry.

The private-asset regression can be run without committing game files:

```bash
ANIVIEWER_CONSTRAINT_ASSET=/path/to/data/xml_bin/monster_ab.bin \
  ./node_modules/.bin/vitest run src/core/edit-constraints.assets.test.ts \
  --reporter=verbose --disableConsoleIntercept

ANIVIEWER_CONSTRAINT_ASSET=/path/to/data/xml_bin/monster_ab.bin \
  ./node_modules/.bin/playwright test tests/edit-constraints.spec.ts \
  -g 'local game asset'
```

The real-asset browser fixture expects the accompanying game's `xml_resources`
and `gfx` directories and AVIF sheets. The pure affine fixture accepts either BIN
or JSON and discovers parent/member relationships from the asset, without
monster-specific runtime branches.

WebKit desktop and mobile-emulation checks have a separate reusable configuration:

```bash
./node_modules/.bin/playwright test --config=playwright.webkit.config.ts \
  tests/edit-constraints.spec.ts tests/sprite-transform.spec.ts
```

Install Playwright's WebKit browser and its platform dependencies first. On an
unsupported Linux distribution, `ANIVIEWER_WEBKIT_EXECUTABLE` can select a local
compatible launcher; no machine-specific path is included in the configuration.

The general verification run passed 162 unit tests (9 optional-fixture skips),
180 Chromium browser checks (18 fixture/platform skips), and type-check/build.
The focused WebKit run passed 20 checks (4 fixture/platform skips). Real-asset
viewport tests were run separately and passed on all three Chromium layouts and
both WebKit profiles. Chromium transform/link checks were rerun after the final
help-text layout adjustment: 31 passed, 5 fixture/platform skips.

The initial `monster_ab.bin` run checked 13 tracks, 65 parent cases, 35,445 sampled
poses, and 822,324 point comparisons. Its maximum absolute point error was
`1.2710574864626038e-13`, with the source document unchanged. This is
floating-point numerical noise, not a claim of universal bit-exact arithmetic or
proof about every monster. Real head-assembly viewport checks passed in desktop,
mobile-sized, and compact Chromium layouts. Browser emulation is not physical
iPhone/iPad/Android validation.

Before keyframe recording is introduced, it needs a separate serialization
contract. Arbitrary affine edits cannot always be expressed by a format with
only independent translation/rotation/scale channels. A future writer must
preserve the complete representable state, explicitly reject an unrepresentable
case, or use a lossless project-level affine edit track. Silently accepting a
small residual or adding hidden future compensation keys is not equivalent to
serializing the visible pose.

## Sources

[^1]: Matt Pharr, Wenzel Jakob, Greg Humphreys. *Physically Based Rendering: From Theory to Implementation*, fourth edition, 2023. [Transformations, sections 3.9.2-3.9.7](https://pbr-book.org/4ed/Geometry_and_Transformations/Transformations). Matrix representation, degenerate inverses, translation, scale, and rotation.
[^2]: Blender Foundation. *Blender 4.2 LTS Manual*. [Child Of Constraint, Technical Note](https://docs.blender.org/manual/en/4.2/animation/constraints/relationship/child_of.html). Direct matrix multiplication when all channels are enabled. The relevant technical note was accessible through the indexed official documentation; full-page retrieval was unavailable.
[^3]: Pixar Animation Studios / OpenUSD. [UsdGeomXformable API: Ordered Operations and Resetting the Transform Stack](https://openusd.org/release/api/class_usd_geom_xformable.html). Explicit transform-operation ordering and inherited-stack control.
