import { describe, expect, it } from 'vitest';
import { dofSpriteMeshBounds, mappedDofSpriteNames, normalizeDofKeyframes } from './dof-bundle';

describe('DOF animation extraction', () => {
  it('uses the final Unity key when a channel repeats a timestamp', () => {
    expect(normalizeDofKeyframes([
      { time: 0, value: 1, keytype: 0 },
      { time: 1, value: 2, keytype: 0 },
      { time: 0, value: 3, keytype: 1 },
    ])).toEqual([
      { time: 0, value: 3, keytype: 1 },
      { time: 1, value: 2, keytype: 0 },
    ]);
  });

  it('collects every sprite candidate in a node-local animation mapping', () => {
    expect(mappedDofSpriteNames(
      [4, 2, 3, 2, -1, 20],
      ['body', 'head', 'wing_01', 'wing_02', 'wing_03'],
    )).toEqual(['wing_03', 'wing_01', 'wing_02']);
  });

  it('decodes pivot-local Unity sprite vertex bounds', () => {
    const positions = new Float32Array([
      0.125, 0.98, 0,
      0.995, 0.98, 0,
      0.125, 0.18, 0,
      0.995, 0.18, 0,
    ]);
    expect(dofSpriteMeshBounds(
      { m_PixelsToUnits: 100 },
      {
        m_VertexData: {
          m_VertexCount: 4,
          m_DataSize: new Uint8Array(positions.buffer),
          m_Channels: [{ stream: 0, offset: 0, format: 0, dimension: 3 }],
        },
      },
    )).toEqual([12.5, 18.000000715255737, 99.50000047683716, 98.00000190734863]);
  });
});
