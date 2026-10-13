import { describe, expect, it } from 'vitest';
import {
  costumeShaderBehavior,
  costumeShaderColor,
  costumeShaderFrame,
  costumeShaderUV,
} from './costume-shader';

describe('costume shader behaviors', () => {
  it('uses authored sheet offsets/divisors rather than evenly slicing sheet-projected sequences', () => {
    const behavior = costumeShaderBehavior('ShaderCostumeE9')!;
    const uv = costumeShaderUV(behavior, 2, [399 / 512, 240 / 512], { x: 0, y: 0, width: 64, height: 64 }, [512, 512]);
    expect(uv).toEqual([.5, 78 / 128]);
    expect(costumeShaderUV(behavior, 0, [0, 0], { x: 0, y: 0, width: 64, height: 64 }, [512, 512])).toEqual([0, 0]);
  });
  it('retains equal-slot mapping for unrotated strip sprites', () => {
    expect(costumeShaderUV(costumeShaderBehavior('ShaderCostumeA3')!, 2, [.3, .6],
      { x: 20, y: 40, width: 20, height: 40 }, [100, 100])).toEqual([2.5 / 7, .5]);
  });
  it('matches the desktop Mammott sequence timing', () => {
    const behavior = costumeShaderBehavior('ShaderCostumeA3')!;
    expect(costumeShaderFrame(behavior, 0.02)).toBe(1);
    expect(costumeShaderFrame(behavior, 0.04)).toBe(2);
    expect(costumeShaderFrame(behavior, 0.6)).toBe(5);
    expect(costumeShaderFrame(behavior, 0.7)).toBe(0);
    expect(costumeShaderFrame(behavior, 4.04)).toBe(2);
  });

  it('evaluates per-channel color-wave parameters', () => {
    const behavior = costumeShaderBehavior('ShaderCostumeACE2')!;
    const color = costumeShaderColor(behavior, 0);
    expect(color[0]).toBeCloseTo(0.95);
    expect(color[1]).toBeGreaterThan(0.975);
    expect(color[2]).toBeGreaterThan(1.09);
    expect(color[3]).toBe(1);
  });
});
