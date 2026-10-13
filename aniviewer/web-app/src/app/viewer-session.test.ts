import { describe, expect, it } from 'vitest';
import type { AnimationDocument } from '../core/types';
import { ViewerSession } from './viewer-session';
import { ALL_EDIT_CHANNELS, applyTransformOperations, type EditConstraint } from '../core/edit-constraints';
import { calculateWorldStates } from '../core/animation';

it('starts costume replacements hidden in both the layer panel and renderer state', () => {
  const session = new ViewerSession();
  const document = animationDocument();
  document.anims[0]!.layers[1]!.visible = false;
  session.setDocument(document);
  expect(session.isLayerVisible(2)).toBe(false);
  expect(session.toggleLayerVisible(2)).toBe(true);
  session.setTrack(1);
  session.setTrack(0);
  expect(session.isLayerVisible(2)).toBe(false);
});

function animationDocument(): AnimationDocument {
  return {
    rev: 6,
    sources: [],
    anims: [
      {
        name: 'Idle',
        width: 100,
        height: 100,
        layers: [
          { name: 'parent', parent: -1, id: 1, src: 0, anchor_x: 0, anchor_y: 0, frames: [{ time: 2 }] },
          { name: 'child', parent: 1, id: 2, src: 0, anchor_x: 0, anchor_y: 0, frames: [] },
        ],
      },
      { name: 'Dance', width: 100, height: 100, layers: [] },
    ],
  };
}

describe('ViewerSession', () => {
  const rule: EditConstraint = { driverId: 1, memberIds: [2], mode: 'rigid', enabled: true, channels: { ...ALL_EDIT_CHANNELS } };
  const evaluate = (session: ViewerSession, time = 0) => applyTransformOperations(session.track!, calculateWorldStates(session.track!, time), session.snapshot().transformOperations);

  it('link/unlink/relink keeps prior poses at every sampled time and never rewrites source keyframes', () => {
    const session = new ViewerSession(), document = animationDocument(), source = JSON.stringify(document);
    document.anims[0]!.layers[0]!.frames = [
      { time: 0, pos: { x: 3, y: 8, immediate: 0 }, rotation: { value: 24, immediate: 0 }, scale: { x: 35, y: 170, immediate: 0 } },
      { time: 2, pos: { x: 33, y: -8, immediate: 0 }, rotation: { value: -40, immediate: 0 }, scale: { x: -50, y: 110, immediate: 0 } },
    ];
    const animatedSource = JSON.stringify(document);
    expect(animatedSource).not.toBe(source);
    session.setDocument(document);
    session.applyLayerEdit(2, { offsetY: 3, rotation: 21 });
    const before = [0, .2, 1, 2].map((time) => evaluate(session, time));
    session.setConstraint(rule);
    expect([0, .2, 1, 2].map((time) => evaluate(session, time))).toEqual(before);
    session.applyLayerEdit(1, { rotation: 32, scaleX: 1.7, offsetX: 14 });
    const linked = [0, .2, 1, 2].map((time) => evaluate(session, time));
    session.setConstraint({ ...rule, enabled: false });
    expect([0, .2, 1, 2].map((time) => evaluate(session, time))).toEqual(linked);
    session.applyLayerEdit(1, { rotation: 61, offsetY: 20 });
    const off = [0, .2, 1, 2].map((time) => evaluate(session, time));
    off.forEach((states, i) => expect(states.get(2)).toEqual(linked[i]!.get(2)));
    session.setConstraint({ ...rule, mode: 'selective', channels: { movement: false, rotation: true, scale: false } });
    expect([0, .2, 1, 2].map((time) => evaluate(session, time))).toEqual(off);
    expect(JSON.stringify(document)).toBe(animatedSource);
    for (let i = 0; i < 3; i++) session.undo();
    expect([0, .2, 1, 2].map((time) => evaluate(session, time))).toEqual(linked);
    for (let i = 0; i < 3; i++) session.redo();
    expect([0, .2, 1, 2].map((time) => evaluate(session, time))).toEqual(off);
  });

  it('rigid members resolve to the driver, hidden members follow, and duplicate selection never double-transforms', () => {
    const session = new ViewerSession(); session.setDocument(animationDocument()); session.setConstraint(rule);
    session.setLayerVisible(2, false);
    expect(session.transformLayerIds([1, 2])).toEqual([1]);
    session.applyLayerEdits(new Map([[1, { offsetX: 10 }], [2, { offsetX: 10 }]]));
    const result = evaluate(session), base = calculateWorldStates(session.track!, 0);
    for (const id of [1, 2]) expect(result.get(id)!.tx).toBe(base.get(id)!.tx + 10);
    expect(session.snapshot().transformOperations).toHaveLength(1);
    session.undo(); expect(evaluate(session)).toEqual(base);
    session.redo(); expect(evaluate(session)).toEqual(result);
  });

  it('preview always starts from the same snapshot; cancel and reset restore exact operation stacks', () => {
    const session = new ViewerSession(); session.setDocument(animationDocument()); session.setConstraint(rule);
    session.applyLayerEdit(1, { offsetX: 12, rotation: 30 });
    const initial = evaluate(session);
    session.beginTransform([1]);
    for (let i = 1; i <= 80; i++) session.previewTransform(new Map([[1, { scaleX: 1 + i / 80 }]]));
    expect(session.snapshot().transformOperations).toHaveLength(2);
    session.cancelTransform(); expect(evaluate(session)).toEqual(initial);
    session.applyLayerEdit(1, { scaleY: .7, rotation: 60 });
    const final = evaluate(session);
    session.resetLayerEdits([2]);
    expect(evaluate(session)).toEqual(calculateWorldStates(session.track!, 0));
    session.undo(); expect(evaluate(session)).toEqual(final);
    session.redo(); expect(session.snapshot().transformOperations).toEqual([]);
  });

  it('invalid graph edits are atomic, snapshots own their data, and changing tracks clears links', () => {
    const session = new ViewerSession(); session.setDocument(animationDocument()); session.setConstraint(rule);
    session.applyLayerEdit(1, { rotation: 40 });
    const before = session.snapshot();
    expect(() => session.setConstraint({ ...rule, driverId: 2, memberIds: [1] })).toThrow(/cycle/);
    expect(session.snapshot()).toEqual(before);
    before.constraints[0]!.channels.rotation = false;
    before.transformOperations[0]!.targets[0]!.channels.rotation = false;
    before.transformOperations[0]!.delta.rotation = -900;
    expect(session.snapshot().constraints[0]!.channels.rotation).toBe(true);
    expect(session.snapshot().transformOperations[0]!.delta.rotation).toBe(40);
    session.setTrack(1);
    expect(session.snapshot().constraints).toEqual([]);
    expect(session.snapshot().transformOperations).toEqual([]);
  });

  it('records an entire multi-layer gesture as one undo command', () => {
    const session = new ViewerSession(); session.setDocument(animationDocument());
    session.beginTransform([1, 2, 999]);
    for (let i = 1; i <= 10; i++) session.previewTransform(new Map([[1, { offsetX: i }], [2, { rotation: i }], [999, { offsetX: 22 }]]));
    expect(session.history.canUndo).toBe(false);
    expect(session.commitTransform('Move and rotate')).toBe(true);
    expect(session.layerEdit(1).offsetX).toBe(10);
    expect(session.layerEdit(2).rotation).toBe(10);
    expect(session.snapshot().layerEdits.has(999)).toBe(false);
    session.undo();
    expect(session.layerEdit(1).offsetX).toBe(0);
    expect(session.layerEdit(2).rotation).toBe(0);
    expect(session.history.canUndo).toBe(false);
    session.redo();
    expect(session.layerEdit(1).offsetX).toBe(10);
    expect(session.layerEdit(2).rotation).toBe(10);
  });

  it('cancelled and no-op gestures preserve the redo stack', () => {
    const session = new ViewerSession(); session.setDocument(animationDocument());
    session.applyLayerEdit(1, { offsetY: 20 }); session.undo();
    session.beginTransform([1]); session.previewTransform(new Map([[1, { scaleX: 2 }]])); session.cancelTransform();
    expect(session.layerEdit(1).scaleX).toBe(1);
    session.beginTransform([1]); expect(session.commitTransform()).toBe(false);
    expect(session.redo()).toBe(true);
    expect(session.layerEdit(1).offsetY).toBe(20);
    session.applyLayerEdit(1, { offsetX: 4 });
    expect(session.redo()).toBe(false);
  });

  it('track changes discard drafts and never transfer an edit to reused layer IDs', () => {
    const session = new ViewerSession(); session.setDocument(animationDocument());
    session.beginTransform([1]); session.previewTransform(new Map([[1, { rotation: 40 }]]));
    session.setTrack(1);
    expect(session.transforming).toBe(false);
    expect(session.snapshot().layerEdits.size).toBe(0);
    expect(session.commitTransform()).toBe(false);
    session.setTrack(0);
    expect(session.layerEdit(1).rotation).toBe(0);
  });

  it('snapshots cannot mutate the session edits outside undo history', () => {
    const session = new ViewerSession(); session.setDocument(animationDocument());
    session.applyLayerEdit(1, { offsetX: 10 });
    session.snapshot().layerEdits.get(1)!.offsetX = 999;
    expect(session.layerEdit(1).offsetX).toBe(10);
  });
  it('owns playback, selection, and visibility state per track', () => {
    const session = new ViewerSession();
    session.setDocument(animationDocument());
    session.setTime(4);
    session.selectLayer(1);
    session.selectLayer(2, true);
    session.setLayerVisible(2, false);

    expect(session.currentTime).toBe(2);
    expect(session.snapshot().selectedLayerIds).toEqual([1, 2]);
    expect(session.isLayerVisible(2)).toBe(false);

    expect(session.setTrack(1)).toBe(true);
    expect(session.currentTime).toBe(0);
    expect(session.snapshot().selectedLayerIds).toEqual([]);
    expect(session.isLayerVisible(2)).toBe(true);
  });

  it('records layer edits through the shared undo and redo history', () => {
    const session = new ViewerSession();
    session.setDocument(animationDocument());

    expect(session.applyLayerEdit(2, { offsetX: 12, scaleY: 1.5 })).toBe(true);
    expect(session.layerEdit(2)).toMatchObject({ offsetX: 12, scaleY: 1.5 });
    expect(session.history.canUndo).toBe(true);

    expect(session.undo()).toBe(true);
    expect(session.layerEdit(2)).toMatchObject({ offsetX: 0, scaleY: 1 });
    expect(session.redo()).toBe(true);
    expect(session.layerEdit(2)).toMatchObject({ offsetX: 12, scaleY: 1.5 });
  });
});
