import { describe, expect, test } from 'vitest';
import {
  buildAnimationAudioCandidates,
  normalizeAudioKey,
  resolveAudioContext,
  shouldSkipAnimationAudio,
} from './audio-resolution';

describe('audio resolution policy', () => {
  test('normalizes paths and animation bundle suffixes', () => {
    expect(normalizeAudioKey('audio/music/03-Dr_Teeth_01.ogg')).toBe('03_dr_teeth_01');
    expect(normalizeAudioKey('bundle://ACDE_riff_idle.ANIMBBB')).toBe('acde_riff_idle');
  });

  test('detects Muppets Show and beta assets without relying only on parser format', () => {
    expect(resolveAudioContext({ detectedFormat: 'muppets' })).toBe('muppets');
    expect(resolveAudioContext({ sourceFormat: 'muppets' })).toBe('muppets');
    expect(resolveAudioContext({ detectedFormat: 'rev2', entryName: 'muppet_rowlf_stage01.bin' })).toBe('muppets');
    expect(resolveAudioContext({ kind: 'dof' })).toBe('dof');
    expect(resolveAudioContext({ sourceFormat: 'rev6', entryName: 'monster_a.bin' })).toBe('standard');
  });

  test('hard-blocks silent utility animations', () => {
    for (const name of ['Idle', 'IdleLoop', 'Store', 'Sleep', 'Market', 'structure_muppetman_level2']) {
      expect(shouldSkipAnimationAudio(name, 'muppets')).toBe(true);
    }
    for (const name of ['A_tweedle_baby_pose', 'A_tweedle_baby_blink', 'A_tweedle_adult_breath']) {
      expect(shouldSkipAnimationAudio(name, 'dof')).toBe(true);
    }
  });

  test('keeps the desktop monster-f dance exception narrow', () => {
    expect(shouldSkipAnimationAudio('Dance', 'standard', 'a')).toBe(true);
    expect(shouldSkipAnimationAudio('F_Dance_01', 'standard', '')).toBe(false);
    expect(shouldSkipAnimationAudio('Dance', 'standard', 'f')).toBe(false);
  });

  test.each([
    ['01-rowlf_01', 'muppet_rowlf_stage01.bin', '01_rawlf_01'],
    ['03-drteeth_01', 'muppet_drteeth_stage03.bin', '03_dr_teeth_01'],
    ['01-guitar_whatnot_01', 'muppet_guitar_whatnot_stage01.bin', '01_accoustic_01'],
    ['01-pot_whatnot_01', 'muppet_pot_whatnot_stage01.bin', '01_pots_01'],
    ['01-twin_whatnot_01', 'muppet_twin_whatnot_stage01.bin', '01_twins_01'],
    ['02-lew_zealand_01', 'muppet_lew_zealand_stage02.bin', '02_lou_01'],
    ['02-marvinsuggs', 'muppet_marvinsuggs_stage02.bin', '02_suggs_01'],
    ['03-misspiggy_01', 'muppet_misspiggy_stage03.bin', '03_piggy_01'],
    ['04-80srobot_01', 'muppet_80srobot_stage04.bin', '04_80s_robot_01'],
    ['04-crazy_harry_01', 'muppet_crazy_harry_stage04.bin', '04_harry_01'],
  ])('maps %s to an exact Muppets clip candidate', (track, entry, expected) => {
    expect(buildAnimationAudioCandidates(track, 'muppets', entry)).toContain(expected);
  });

  test('does not generate weak candidates for standard animations', () => {
    expect(buildAnimationAudioCandidates('Monster A Activate', 'standard')).toEqual(['monster_a_activate']);
  });

  test('keeps generic one-shot samples out of stage performance candidates', () => {
    const rowlf = buildAnimationAudioCandidates(
      '01-rowlf_01',
      'muppets',
      'muppet_rowlf_stage01.bin',
    );
    expect(rowlf).toContain('01_rawlf_01');
    expect(rowlf).not.toContain('rowlf');

    const chickens = buildAnimationAudioCandidates(
      '03-chickens_01',
      'muppets',
      'muppet_chicken_stage03.bin',
    );
    expect(chickens).not.toContain('chickens');
  });
});
