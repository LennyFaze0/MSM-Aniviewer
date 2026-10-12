import { expect, it } from 'vitest';
import { ViewerSession } from './viewer-session';
import type { IKRig } from '../core/ik-constraints';

const rig: IKRig = { id: 'test', name: 'Test', enabled: true, bend: 'source', fallbackBend: 1,
  target: { space: 'offset', x: 0, y: 0 }, bones: [{ layerId: 0, start: { x: 0, y: 0 }, end: { x: 20, y: 0 }, followers: [] }],
};
function session() {
  const session = new ViewerSession();
  const track = { name: 'Test', width: 100, height: 100, layers: [{ id: 0, name: 'Bone', src: 0, parent: -1, anchor_x: 0, anchor_y: 0, frames: [{ time: 0 }, { time: 1 }] }] };
  session.setDocument({ rev: 6, sources: [], anims: [track, { ...track, name: 'Other' }] }); session.setIKRig(rig);
  return session;
}
it('IK drag previews do not accumulate and commit as one shared history command', () => {
  const s = session(); expect(s.beginIKTarget('test')).toBe(true);
  for (let i = 0; i <= 80; i++) s.previewIKTarget(i, -i);
  expect(s.transforming).toBe(true); expect(s.snapshot().ikRigs[0]!.target).toEqual({ space: 'offset', x: 80, y: -80 });
  expect(s.commitIKTarget()).toBe(true); expect(s.transforming).toBe(false);
  s.undo(); expect(s.snapshot().ikRigs).toEqual([rig]);
  s.undo(); expect(s.snapshot().ikRigs).toEqual([]);
  s.redo(); s.redo(); expect(s.snapshot().ikRigs[0]!.target.x).toBe(80);
});
it('IK cancel restores exact data and redo; disabling is undoable; track changes clear everything', () => {
  const s = session(); s.setIKRig({ ...rig, enabled: false }); s.undo();
  s.beginIKTarget('test'); s.previewIKTarget(20, 30); s.cancelTransform();
  expect(s.snapshot().ikRigs).toEqual([rig]); expect(s.commitIKTarget()).toBe(false);
  s.redo(); expect(s.beginIKTarget('test')).toBe(false); expect(s.snapshot().ikRigs[0]!.enabled).toBe(false);
  s.undo(); s.beginIKTarget('test'); s.previewIKTarget(30, 40); s.setTrack(1);
  expect(s.snapshot().ikRigs).toEqual([]); expect(s.transforming).toBe(false); expect(s.history.canUndo).toBe(false);
});
it('rig snapshots and inputs cannot mutate history; invalid ownership changes are rejected atomically', () => {
  const s = session(), before = s.snapshot();
  expect(() => s.setIKRig({ ...rig, id: 'collision' })).toThrow(/two IK/);
  expect(s.snapshot()).toEqual(before);
  before.ikRigs[0]!.target.x = 999; before.ikRigs[0]!.bones[0]!.start.x = 123;
  expect(s.snapshot().ikRigs).toEqual([rig]);
  s.applyLayerEdit(0, { rotation: 45 }); s.removeIKRig('test'); s.undo();
  expect(s.snapshot().ikRigs).toEqual([rig]); expect(s.layerEdit(0).rotation).toBe(45);
});
