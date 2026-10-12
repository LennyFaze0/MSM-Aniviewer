import { describe, expect, it } from 'vitest';
import {
  animationDuration,
  animationRenderOrder,
  animationUsesDepth,
  calculateWorldStates,
  evaluateLayer,
} from './animation';
import type { AnimationLayer, AnimationTrack } from './types';

function layer(overrides: Partial<AnimationLayer> = {}): AnimationLayer {
  return {
    name: 'body',
    parent: -1,
    id: 0,
    src: 0,
    anchor_x: 0,
    anchor_y: 0,
    frames: [],
    ...overrides,
  };
}

describe('animation evaluation', () => {
  it('interpolates linear channels and preserves stepped channels', () => {
    const subject = layer({
      frames: [
        {
          time: 0,
          pos: { immediate: 0, x: 0, y: 10 },
          opacity: { immediate: 1, value: 25 },
        },
        {
          time: 2,
          pos: { immediate: 0, x: 20, y: 30 },
          opacity: { immediate: 1, value: 100 },
        },
      ],
    });

    const state = evaluateLayer(subject, 1);
    expect(state.posX).toBe(10);
    expect(state.posY).toBe(20);
    expect(state.opacity).toBe(25);
  });

  it('expands numeric sprite sequences without changing suffix width', () => {
    const subject = layer({
      frames: [
        { time: 0, sprite: { immediate: 0, string: 'A_tail_01' } },
        { time: 1, sprite: { immediate: 1, string: 'A_tail_05' } },
      ],
    });

    expect(evaluateLayer(subject, 0.6).spriteName).toBe('A_tail_03');
  });

  it('expands DOF sprite sequences while preserving their file extension and pivot', () => {
    const subject = layer({
      anchor_x: 10,
      anchor_y: 20,
      sprite_anchor_map: {
        'A_wing_01.png': [10, 20],
        'A_wing_02.png': [30, 40],
        'A_wing_03.png': [50, 60],
      },
      frames: [
        {
          time: 0,
          pos: { immediate: 1, x: 100, y: 120 },
          sprite: { immediate: 0, string: 'A_wing_01.png' },
        },
        { time: 1, sprite: { immediate: 1, string: 'A_wing_03.png' } },
      ],
    });
    const track: AnimationTrack = {
      name: 'dof-sprite-swap',
      width: 100,
      height: 100,
      layers: [subject],
    };

    expect(evaluateLayer(subject, 0.6).spriteName).toBe('A_wing_02.png');
    expect(calculateWorldStates(track, 0.6).get(0)).toMatchObject({
      spriteName: 'A_wing_02.png',
      tx: 70,
      ty: 80,
    });
  });

  it('composes child matrices while keeping opacity independent', () => {
    const parent = layer({
      name: 'root',
      id: 0,
      frames: [
        {
          time: 0,
          pos: { immediate: 0, x: 10, y: 20 },
          opacity: { immediate: 1, value: 0 },
        },
      ],
    });
    const child = layer({
      name: 'child',
      id: 1,
      parent: 0,
      frames: [
        {
          time: 0,
          pos: { immediate: 0, x: 5, y: 6 },
          opacity: { immediate: 1, value: 100 },
        },
      ],
    });
    const track: AnimationTrack = {
      name: 'test',
      width: 100,
      height: 100,
      centered: 1,
      layers: [parent, child],
    };

    const state = calculateWorldStates(track, 0).get(1);
    expect(state?.tx).toBe(15);
    expect(state?.ty).toBe(26);
    expect(state?.opacity).toBe(100);
  });

  it('derives duration from the latest frame across all layers', () => {
    const track: AnimationTrack = {
      name: 'test',
      width: 100,
      height: 100,
      layers: [
        layer({ frames: [{ time: 1 }] }),
        layer({ id: 1, frames: [{ time: 3.25 }] }),
      ],
    };
    expect(animationDuration(track)).toBe(3.25);
  });

  it('orders depth-aware layers back to front with stable ties', () => {
    const track: AnimationTrack = {
      name: 'depth',
      width: 100,
      height: 100,
      layers: [
        layer({ id: 0, frames: [{ time: 0, depth: { immediate: 1, value: 5 } }] }),
        layer({ id: 1, frames: [{ time: 0, depth: { immediate: 1, value: -2 } }] }),
        layer({ id: 2, frames: [{ time: 0, depth: { immediate: 1, value: -2 } }] }),
      ],
    };
    const states = calculateWorldStates(track, 0);

    expect(animationUsesDepth(track)).toBe(true);
    expect(animationRenderOrder(track, states).map(({ id }) => id)).toEqual([1, 2, 0]);
  });

  it('keeps legacy front-to-back layer lists reversed without depth channels', () => {
    const track: AnimationTrack = {
      name: 'legacy',
      width: 100,
      height: 100,
      layers: [layer({ id: 0 }), layer({ id: 1 }), layer({ id: 2 })],
    };

    expect(animationUsesDepth(track)).toBe(false);
    expect(animationRenderOrder(track, calculateWorldStates(track, 0)).map(({ id }) => id))
      .toEqual([2, 1, 0]);
  });
});
