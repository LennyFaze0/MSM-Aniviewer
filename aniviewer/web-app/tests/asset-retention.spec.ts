import { expect, test, type Page } from '@playwright/test';
import { nativeRev6Fixture, zippedNativeFixture } from './native-fixture';

function assets(track = 'Idle') {
  const fixture = nativeRev6Fixture();
  const bin = Buffer.from(fixture.bin);
  bin.write(track, bin.indexOf('Idle'), 'utf8');
  return [
    { name: 'monster_test.bin', mimeType: 'application/octet-stream', buffer: bin },
    { name: 'atlas.xml', mimeType: 'application/xml', buffer: Buffer.from(fixture.xml) },
    { name: 'atlas.png', mimeType: 'image/png', buffer: fixture.png },
  ];
}

async function stored(page: Page) {
  return page.evaluate(async () => {
    const { RetainedAssetStore } = await import('/MSM-Aniviewer/src/core/retained-assets.ts');
    const snapshot = await new RetainedAssetStore().read();
    return { paths: snapshot.files.map((file) => file.path), folders: snapshot.folders.length, state: snapshot.state };
  });
}

async function enableRetention(page: Page) {
  await page.locator('#controlTab-files').click();
  await page.locator('#rememberAssetsCheckbox').check();
  await expect(page.locator('#assetRetentionPanel')).toHaveAttribute('data-enabled', 'true');
  await expect(page.locator('#assetRetentionPanel')).toHaveAttribute('data-busy', 'false');
}

async function installNativeFolderFixture(page: Page) {
  await page.addInitScript(() => {
    Object.defineProperty(window, 'showDirectoryPicker', {
      configurable: true,
      value: async (options: { mode: string }) => {
        if (options.mode !== 'read') throw new Error('Viewer requested write access');
        return (await navigator.storage.getDirectory()).getDirectoryHandle('Game', { create: true });
      },
    });
    // Real structured-cloneable directory handles, with controllable permission state.
    Object.defineProperty(FileSystemHandle.prototype, 'queryPermission', {
      configurable: true,
      value: async () => localStorage.getItem('test-permission') ?? 'granted',
    });
    Object.defineProperty(FileSystemHandle.prototype, 'requestPermission', {
      configurable: true,
      value: async () => {
        localStorage.setItem('test-permission-requests', String(Number(localStorage.getItem('test-permission-requests') ?? 0) + 1));
        localStorage.setItem('test-permission', 'granted');
        return 'granted';
      },
    });
    const values = FileSystemDirectoryHandle.prototype.values;
    FileSystemDirectoryHandle.prototype.values = async function* () {
      for await (const entry of values.call(this)) {
        if (this.name === 'Game' && localStorage.getItem('test-scan-fails')) throw new Error('Simulated disconnected folder');
        yield entry;
      }
    };
  });
  await page.goto('.');
  await writeNativeFolder(page, assets());
}

async function writeNativeFolder(page: Page, files: ReturnType<typeof assets>) {
  await page.evaluate(async (records) => {
    const directory = await (await navigator.storage.getDirectory()).getDirectoryHandle('Game', { create: true });
    for (const record of records) {
      const handle = await directory.getFileHandle(record.name, { create: true });
      const stream = await handle.createWritable();
      await stream.write(new Uint8Array(record.data));
      await stream.close();
    }
  }, files.map((file) => ({ name: file.name, data: [...file.buffer] })));
}

test('retention is opt-in and forgotten copies do not affect the current viewport', async ({ page }) => {
  await page.goto('.');
  await page.locator('#fileInput').setInputFiles(assets());
  await expect(page.locator('#statusText')).toHaveText('1 animation loaded from BIN');
  expect((await stored(page)).paths).toEqual([]);
  await page.reload();
  await expect(page.locator('#viewportEmpty')).toBeVisible();
  await page.locator('#fileInput').setInputFiles(assets());
  await expect(page.locator('#statusText')).toHaveText('1 animation loaded from BIN');
  await enableRetention(page);
  await expect.poll(async () => (await stored(page)).paths.length).toBe(3);
  page.once('dialog', (dialog) => dialog.accept());
  await page.locator('#clearSavedAssetsButton').click();
  await expect(page.locator('#assetRetentionPanel')).toHaveAttribute('data-enabled', 'false');
  await expect(page.locator('#viewportEmpty')).toBeHidden();
  expect((await stored(page)).paths).toEqual([]);
  await page.reload();
  await expect(page.locator('#viewportEmpty')).toBeVisible();
});

test('restores raw ZIP assets and the last selected animation without a folder API', async ({ page }) => {
  await page.addInitScript(() => Object.defineProperty(window, 'showDirectoryPicker', { configurable: true, value: undefined }));
  await page.goto('.');
  await page.locator('#fileInput').setInputFiles([
    { name: 'game.zip', mimeType: 'application/zip', buffer: await zippedNativeFixture() },
    ...assets('Wave'),
  ]);
  await expect(page.locator('#statusText')).toHaveText('1 animation loaded from BIN');
  await enableRetention(page);
  await page.locator('#animationFileSelect').selectOption('1');
  await expect(page.locator('#trackList .row-name')).toHaveText('Wave');
  await expect.poll(async () => (await stored(page)).state.selection?.path).toBe('monster_test.bin');
  expect((await stored(page)).paths).toContain('game.zip');
  expect((await stored(page)).paths).not.toContain('game.zip/data/xml_bin/monster_test.bin');
  await page.reload();
  await expect(page.locator('#trackList .row-name')).toHaveText('Wave');
  await page.locator('#controlTab-files').click();
  await page.locator('#animationFileSelect').selectOption('0');
  await expect(page.locator('#trackList .row-name')).toHaveText('Idle');
  await expect(page.locator('#viewportEmpty')).toBeHidden();
});

test('reimporting a same-name animation replaces its saved bytes', async ({ page }) => {
  await page.goto('.');
  await page.locator('#fileInput').setInputFiles(assets());
  await expect(page.locator('#trackList .row-name')).toHaveText('Idle');
  await enableRetention(page);
  await page.locator('#fileInput').setInputFiles([assets('Wave')[0]!]);
  await expect(page.locator('#trackList .row-name')).toHaveText('Wave');
  await expect(page.locator('#assetRetentionPanel')).toHaveAttribute('data-busy', 'false');
  await page.reload();
  await expect(page.locator('#trackList .row-name')).toHaveText('Wave');
  expect((await stored(page)).paths).toHaveLength(3);
});

test('saved read-only folder handles refresh changed, added, and removed assets on load', async ({ page }) => {
  await installNativeFolderFixture(page);
  await writeNativeFolder(page, [{ ...assets()[2]!, name: 'unused.png' }]);
  await page.locator('#importFolderButton').click();
  await expect(page.locator('#trackList .row-name')).toHaveText('Idle');
  await enableRetention(page);
  expect((await stored(page)).folders).toBe(1);
  await writeNativeFolder(page, [assets('Wave')[0]!, { ...assets()[0]!, name: 'monster_extra.bin' }]);
  await page.evaluate(async () => {
    const directory = await (await navigator.storage.getDirectory()).getDirectoryHandle('Game');
    await directory.removeEntry('unused.png');
  });
  await page.reload();
  await expect(page.locator('#trackList .row-name')).toHaveText('Wave');
  await expect(page.locator('#assetRetentionPanel')).toHaveAttribute('data-busy', 'false');
  const snapshot = await stored(page);
  expect(snapshot.paths).toContain('game/monster_extra.bin');
  expect(snapshot.paths).not.toContain('game/unused.png');
  expect(snapshot.folders).toBe(1);
});

test('missing permission uses cached assets until the user explicitly reconnects', async ({ page }) => {
  await installNativeFolderFixture(page);
  await page.locator('#importFolderButton').click();
  await expect(page.locator('#trackList .row-name')).toHaveText('Idle');
  await enableRetention(page);
  await writeNativeFolder(page, [assets('Wave')[0]!]);
  await page.evaluate(() => localStorage.setItem('test-permission', 'prompt'));
  await page.reload();
  await expect(page.locator('#assetRetentionPanel')).toHaveAttribute('data-busy', 'false');
  await expect(page.locator('#trackList .row-name')).toHaveText('Idle');
  expect(await page.evaluate(() => localStorage.getItem('test-permission-requests'))).toBeNull();
  await page.locator('#controlTab-files').click();
  await expect(page.locator('.asset-retention-folders')).toContainText('Permission needed');
  await page.getByRole('button', { name: 'Reconnect game', exact: true }).click();
  await expect(page.locator('#trackList .row-name')).toHaveText('Wave');
  expect(await page.evaluate(() => localStorage.getItem('test-permission-requests'))).toBe('1');
});

test('failed scans keep the complete previous cached folder', async ({ page }) => {
  await installNativeFolderFixture(page);
  await page.locator('#importFolderButton').click();
  await expect(page.locator('#trackList .row-name')).toHaveText('Idle');
  await enableRetention(page);
  await writeNativeFolder(page, [assets('Wave')[0]!]);
  await page.evaluate(() => localStorage.setItem('test-scan-fails', 'true'));
  await page.reload();
  await expect(page.locator('#assetRetentionPanel')).toHaveAttribute('data-busy', 'false');
  await expect(page.locator('#trackList .row-name')).toHaveText('Idle');
  await expect(page.locator('.asset-retention-folders')).toContainText('Refresh failed; saved copy kept');
  expect((await stored(page)).paths).toHaveLength(3);
});

test('a failed cache write rolls back and can be retried without losing imported assets', async ({ page }) => {
  await page.goto('.');
  await page.locator('#fileInput').setInputFiles(assets());
  await expect(page.locator('#trackList .row-name')).toHaveText('Idle');
  await enableRetention(page);
  await page.evaluate(() => {
    const original = IDBObjectStore.prototype.put;
    IDBObjectStore.prototype.put = function (...args) {
      if (this.name === 'files') {
        IDBObjectStore.prototype.put = original;
        throw new DOMException('Simulated full storage', 'QuotaExceededError');
      }
      return original.apply(this, args);
    };
  });
  await page.locator('#fileInput').setInputFiles([assets('Wave')[0]!]);
  await expect(page.locator('#trackList .row-name')).toHaveText('Wave');
  await expect(page.locator('#assetRetentionStatus')).toContainText('NOT saved');
  const oldBytes = await page.evaluate(async () => {
    const { RetainedAssetStore } = await import('/MSM-Aniviewer/src/core/retained-assets.ts');
    const snapshot = await new RetainedAssetStore().read();
    return snapshot.files.find((file) => file.name === 'monster_test.bin')!.blob.text();
  });
  expect(oldBytes).toContain('Idle');
  await page.locator('#controlTab-files').click();
  await page.locator('#retrySaveAssetsButton').click();
  await expect(page.locator('#assetRetentionPanel')).toHaveAttribute('data-busy', 'false');
  await page.reload();
  await expect(page.locator('#trackList .row-name')).toHaveText('Wave');
});

test('an invalid replacement does not overwrite the working animation or saved library', async ({ page }) => {
  await page.goto('.');
  await page.locator('#fileInput').setInputFiles(assets());
  await expect(page.locator('#trackList .row-name')).toHaveText('Idle');
  await enableRetention(page);
  await page.locator('#fileInput').setInputFiles({ name: 'monster_test.bin', mimeType: 'application/octet-stream', buffer: Buffer.from([0]) });
  await expect(page.locator('#assetRetentionStatus')).toContainText('Could not finish');
  await expect(page.locator('#trackList .row-name')).toHaveText('Idle');
  await expect(page.locator('#viewportEmpty')).toBeHidden();
  await page.reload();
  await expect(page.locator('#trackList .row-name')).toHaveText('Idle');
});

test('retention controls are reachable from Settings and restore the selected track', async ({ page }) => {
  await page.goto('.');
  await page.locator('#settingsButton').click();
  await page.locator('[data-settings-tab="paths"]').click();
  await page.getByRole('button', { name: 'Manage retained assets' }).click();
  await expect(page.locator('#rememberAssetsCheckbox')).toBeFocused();
  await expect(page.locator('#settingsLayer')).toBeHidden();
  const document = {
    rev: 6, sources: [],
    anims: ['First', 'Second'].map((name) => ({ name, width: 64, height: 64, layers: [] })),
  };
  await page.locator('#fileInput').setInputFiles({ name: 'tracks.json', mimeType: 'application/json', buffer: Buffer.from(JSON.stringify(document)) });
  await expect(page.locator('#trackList [data-track-index]')).toHaveCount(2);
  await enableRetention(page);
  await page.locator('#controlTab-animation').click();
  await page.locator('#trackList [data-track-index="1"]').click();
  await expect.poll(async () => (await stored(page)).state.selection?.track).toBe(1);
  await page.reload();
  await expect(page.locator('#trackList [data-track-index="1"]')).toHaveClass(/active/);
});
