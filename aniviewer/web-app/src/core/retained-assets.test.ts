import { describe, expect, test } from 'vitest';
import {
  assetPath, folderPermission, isRetainableAsset, mergeFolderSnapshot, restoreFile, retainFile, scanAssetFolder,
  type AssetFolderHandle, type RetainedFolder,
} from './retained-assets';

function folder(): RetainedFolder {
  return { id: 'one', root: 'game', handle: { kind: 'directory', name: 'game' } as AssetFolderHandle };
}

describe('retained asset snapshots', () => {
  test('round trips file bytes, MIME, modification time, and relative paths', async () => {
    const original = new File(['atlas'], 'Atlas.XML', { type: 'application/xml', lastModified: 12345 });
    Object.defineProperty(original, 'webkitRelativePath', { value: 'Game/Data/Atlas.XML' });
    const restored = restoreFile(retainFile(original));
    expect(assetPath(restored)).toBe('game/data/atlas.xml');
    expect(restored.name).toBe('Atlas.XML');
    expect(restored.lastModified).toBe(12345);
    expect(restored.type).toBe('application/xml');
    expect(await restored.text()).toBe('atlas');
  });

  test('only considers usable assets, not entire game executables or system files', () => {
    for (const name of ['monster.bin', 'assets.ZIP', 'atlas.avif', 'sound.ogg', 'atlas.xml', '__data', 'staticinfo.bundle']) {
      expect(isRetainableAsset(name)).toBe(true);
    }
    for (const name of ['game.exe', 'library.dll', 'crash.log', '.DS_Store']) expect(isRetainableAsset(name)).toBe(false);
  });

  test('deletes only files owned by the successfully scanned folder', () => {
    const a = { ...retainFile(new File(['a'], 'old.bin', { lastModified: 1 }), 'one'), path: 'game/old.bin' };
    const b = { ...retainFile(new File(['b'], 'else.bin'), 'two'), path: 'other/else.bin' };
    const loose = retainFile(new File(['c'], 'loose.bin'));
    const fresh = { ...a, path: 'game/new.bin' };
    const result = mergeFolderSnapshot(new Map([a, b, loose].map((file) => [file.path, file])), folder(), [fresh]);
    expect(result.deletes).toEqual(['game/old.bin']);
    expect([...result.files.keys()]).toEqual(['other/else.bin', 'loose.bin', 'game/new.bin']);
  });

  test('metadata changes trigger replacement while unchanged blobs are reused', () => {
    const old = { ...retainFile(new File(['a'], 'monster.bin', { lastModified: 1 }), 'one'), path: 'game/monster.bin' };
    const current = new Map([[old.path, old]]);
    expect(mergeFolderSnapshot(current, folder(), [{ ...old }]).puts).toEqual([]);
    expect(mergeFolderSnapshot(current, folder(), [{ ...old, lastModified: 2 }]).puts).toHaveLength(1);
    expect(mergeFolderSnapshot(current, folder(), [{ ...old, blob: new Blob(['longer']) }]).puts).toHaveLength(1);
  });

  test('an incomplete folder scan rejects instead of publishing a partial replacement', async () => {
    const broken = folder();
    broken.handle.values = async function* () {
      yield { kind: 'file', name: 'new.bin', getFile: async () => new File(['new'], 'new.bin') };
      throw new Error('Folder disconnected');
    };
    await expect(scanAssetFolder(broken)).rejects.toThrow('Folder disconnected');
  });

  test('does not infer permission from a stored path or a missing permission API', async () => {
    expect(await folderPermission(folder().handle)).toBe('prompt');
    expect(await folderPermission({ ...folder().handle, queryPermission: async () => { throw new Error(); } })).toBe('denied');
  });
});
