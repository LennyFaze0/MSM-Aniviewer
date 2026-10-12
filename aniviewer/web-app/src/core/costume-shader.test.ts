import { describe, expect, it } from 'vitest';
import {
  costumeShaderBehavior,
  costumeShaderColor,
  costumeShaderFrame,
} from './costume-shader';

describe('costume shader behaviors', () => {
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
