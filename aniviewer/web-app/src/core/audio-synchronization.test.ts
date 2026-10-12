import { describe, expect, test } from 'vitest';
import {
  animationCycleForAudio,
  animationTimeForAudio,
  audioTimeForAnimation,
  DEFAULT_ANIMATION_AUDIO_TIMING,
  resolveAnimationAudioTiming,
} from './audio-synchronization';

describe('animation/audio synchronization', () => {
  test('fits a Muppets animation cycle to its full staged clip', () => {
    const timing = resolveAnimationAudioTiming(8.083333015, 8.727279);
    expect(timing.loopMultiplier).toBe(1);
    expect(timing.animationPlaybackRate).toBeCloseTo(0.9262146, 6);
    expect(audioTimeForAnimation(8.083333015, 8.083333015, timing, 0)).toBeCloseTo(8.727279, 6);
  });

  test('maps audio that spans two animation cycles without restarting halfway', () => {
    const timing = resolveAnimationAudioTiming(2.083333254, 4.363628);
    expect(timing.loopMultiplier).toBe(2);
    expect(timing.animationPlaybackRate).toBeCloseTo(0.9548629, 6);
    expect(audioTimeForAnimation(0, 2.083333254, timing, 1)).toBeCloseTo(2.181814, 5);
    expect(audioTimeForAnimation(2.083333254, 2.083333254, timing, 1)).toBeCloseTo(4.363628, 6);
  });

  test('returns neutral timing for unavailable metadata', () => {
    expect(resolveAnimationAudioTiming(8, Number.NaN)).toEqual(DEFAULT_ANIMATION_AUDIO_TIMING);
    expect(resolveAnimationAudioTiming(0, 8)).toEqual(DEFAULT_ANIMATION_AUDIO_TIMING);
  });

  test('maps the audio master clock into consecutive Muppets cycles without wrapping audio', () => {
    const timing = resolveAnimationAudioTiming(2, 4.1);
    expect(animationTimeForAudio(1.025, 2, timing, 0)).toBeCloseTo(1);
    expect(animationTimeForAudio(2.2, 2, timing, 0)).toBeGreaterThan(2);
    expect(animationTimeForAudio(2.2, 2, timing, 1)).toBeCloseTo(0.14634146);
    expect(animationTimeForAudio(4.11, 2, timing, 1)).toBeGreaterThan(2);
  });

  test('round trips a scrub position through the audio clock', () => {
    for (const timing of [DEFAULT_ANIMATION_AUDIO_TIMING, resolveAnimationAudioTiming(2, 4.1)]) {
      for (let cycle = 0; cycle < timing.loopMultiplier; cycle++) {
        expect(animationTimeForAudio(audioTimeForAnimation(0.7, 2, timing, cycle), 2, timing, cycle)).toBeCloseTo(0.7);
      }
    }
  });

  test('derives the sub-cycle from natively looping audio, including skipped frame callbacks', () => {
    const timing = resolveAnimationAudioTiming(2, 4.1);
    expect(animationCycleForAudio(0, 2, timing)).toBe(0);
    expect(animationCycleForAudio(2.5, 2, timing)).toBe(1);
    expect(animationCycleForAudio(4.1, 2, timing)).toBe(1);
    expect(animationCycleForAudio(0.5, 2, timing)).toBe(0);
    expect(animationCycleForAudio(3, 2, timing)).toBe(1);
  });
});
