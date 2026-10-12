import { describe, expect, test } from 'vitest';
import type { DofManifestEntry } from './dof-asset-grabber';
import {
  captureDofDownloadSnapshot,
  matchesDownloadedBundleName,
  scanDownloadedDofBundles,
  type DofDownloadDirectoryHandle,
  type DofDownloadFileHandle,
} from './dof-download-import';

function entry(name: string, size: number): DofManifestEntry {
  return { name, size, url: `https://example.test/${name}` };
}

function directory(files: File[]): DofDownloadDirectoryHandle {
  return {
    kind: 'directory',
    name: 'Downloads',
    async *values() {
      for (const file of files) {
        yield {
          kind: 'file',
          name: file.name,
          getFile: async () => file,
        } satisfies DofDownloadFileHandle;
      }
    },
  };
}

describe('DOF downloaded bundle discovery', () => {
  test('matches exact and browser-renamed bundle files only', () => {
    expect(matchesDownloadedBundleName('particles.unity3d', 'particles.unity3d')).toBe(true);
    expect(matchesDownloadedBundleName('particles (12).unity3d', 'particles.unity3d')).toBe(true);
    expect(matchesDownloadedBundleName('PARTICLES (1).UNITY3D', 'particles.unity3d')).toBe(true);
    expect(matchesDownloadedBundleName('particles-copy.unity3d', 'particles.unity3d')).toBe(false);
    expect(matchesDownloadedBundleName('particles (1).unity3d.crdownload', 'particles.unity3d')).toBe(false);
  });

  test('ignores old and incomplete files, then restores canonical bundle names', async () => {
    const expected = [entry('staticinfo_a_tweedle.unity3d', 4), entry('particles.unity3d', 3)];
    const oldMonster = new File([new Uint8Array(4)], 'staticinfo_a_tweedle.unity3d', { lastModified: 100 });
    const oldParticles = new File([new Uint8Array(3)], 'particles.unity3d', { lastModified: 100 });
    const baseline = await captureDofDownloadSnapshot(directory([oldMonster, oldParticles]), expected);
    const renamedMonster = new File([new Uint8Array(4)], 'staticinfo_a_tweedle (1).unity3d', { lastModified: 200 });
    const incompleteParticles = new File([new Uint8Array(2)], 'particles (1).unity3d', { lastModified: 200 });

    const partial = await scanDownloadedDofBundles(
      directory([oldMonster, oldParticles, renamedMonster, incompleteParticles]),
      expected,
      baseline,
    );
    expect(partial.files.map((file) => file.name)).toEqual(['staticinfo_a_tweedle.unity3d']);
    expect(partial.missing.map((item) => item.name)).toEqual(['particles.unity3d']);

    const completeParticles = new File([new Uint8Array(3)], 'particles (1).unity3d', { lastModified: 300 });
    const complete = await scanDownloadedDofBundles(
      directory([oldMonster, oldParticles, renamedMonster, completeParticles]),
      expected,
      baseline,
    );
    expect(complete.files.map((file) => file.name)).toEqual([
      'staticinfo_a_tweedle.unity3d',
      'particles.unity3d',
    ]);
    expect(complete.missing).toEqual([]);
  });
});
