import { describe, expect, test } from 'vitest';
import {
  buildDofBundleUrl,
  buildDofManifestUrl,
  filterDofManifest,
  parseDofBuildHistory,
  parseDofManifest,
  rememberDofBuild,
} from './dof-asset-grabber';

const base = 'https://example.test/dlc/';

describe('DOF asset grabber manifest handling', () => {
  test('builds encoded manifest and bundle URLs', () => {
    expect(buildDofManifestUrl(base, 'build key', 'ios')).toBe(
      'https://example.test/dlc/build%20key/ios/bundles.json',
    );
    expect(buildDofBundleUrl(base, 'build key', 'android', 'staticinfo_a.unity3d')).toBe(
      'https://example.test/dlc/build%20key/android/staticinfo_a.unity3d',
    );
  });

  test('parses object and array manifests while rejecting unsafe names', () => {
    const objectEntries = parseDofManifest({
      'staticinfo_a.unity3d': { size: 42 },
      '../escape.unity3d': { size: 100 },
    }, base, 'build', 'ios');
    expect(objectEntries).toHaveLength(1);
    expect(objectEntries[0]).toMatchObject({ name: 'staticinfo_a.unity3d', size: 42 });

    const arrayEntries = parseDofManifest([
      { name: 'sounds.unity3d', size: 84 },
      { bundle: 'staticinfo_b.unity3d', size: 96 },
    ], base, 'build', 'android');
    expect(arrayEntries.map((entry) => entry.name)).toEqual(['sounds.unity3d', 'staticinfo_b.unity3d']);
  });

  test('filters staticinfo bundles and persists unique recent builds', () => {
    const entries = parseDofManifest({
      'staticinfo_a_tweedle.unity3d': { size: 1 },
      'staticinfo_b_mammott.unity3d': { size: 2 },
      'sounds.unity3d': { size: 3 },
    }, base, 'build', 'ios');
    expect(filterDofManifest(entries, 'tweedle', true).map((entry) => entry.name)).toEqual([
      'staticinfo_a_tweedle.unity3d',
    ]);

    const remembered = rememberDofBuild([{ buildKey: 'old', platform: 'ios' }], 'new', 'android');
    expect(parseDofBuildHistory(JSON.stringify(remembered))).toEqual([
      { buildKey: 'new', platform: 'android' },
      { buildKey: 'old', platform: 'ios' },
    ]);
  });
});
