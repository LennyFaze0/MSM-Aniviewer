import { expect, test } from '@playwright/test';
import { readFileSync } from 'node:fs';
import { nativeRev6Fixture } from '../tests/native-fixture';

// Reuse real-sample audio checks without dev-server-only module imports.
import '../tests/buffered-audio-playback.spec';

test('published shell, settings, assets and decoder load on desktop and phones', async ({ page }) => {
  const errors: string[] = [];
  page.on('pageerror', (error) => errors.push(error.message));
  page.on('response', (response) => {
    if (response.status() >= 400) errors.push(`${response.status()} ${response.url()}`);
  });
  await page.goto('.');
  await expect(page.locator('.viewport-canvas')).toBeVisible();
  await expect(page.locator('#importFilesButton')).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth)).toBe(true);
  await page.locator('#settingsButton').click();
  await expect(page.locator('#settingsLayer')).toHaveAttribute('data-open', 'true');
  await page.keyboard.press('Escape');
  await expect(page.locator('#settingsLayer')).toBeHidden();
  const assets = await page.evaluate(async () => {
    const wasm = await fetch(new URL('wasm/texture2ddecoder.wasm', location.href));
    const magic = Array.from(new Uint8Array(await wasm.arrayBuffer()).slice(0, 4));
    const icon = document.querySelector<HTMLLinkElement>('#appIcon')!.href;
    const iconResponse = await fetch(icon);
    const worker = await fetch(new URL('sw.js', location.href));
    return { magic, icon: iconResponse.ok, worker: worker.ok };
  });
  expect(assets).toEqual({ magic: [0, 97, 115, 109], icon: true, worker: true });
  expect(errors).toEqual([]);
});

test('published parser worker renders local assets and exports a PNG', async ({ page }) => {
  const fixture = nativeRev6Fixture();
  await page.goto('.');
  await page.locator('#fileInput').setInputFiles([
    { name: 'monster_test.bin', mimeType: 'application/octet-stream', buffer: Buffer.from(fixture.bin) },
    { name: 'atlas.xml', mimeType: 'application/xml', buffer: Buffer.from(fixture.xml) },
    { name: 'atlas.png', mimeType: 'image/png', buffer: fixture.png },
  ]);
  await expect(page.locator('#statusText')).toHaveText('1 animation loaded from BIN');
  await expect(page.locator('#layerList [data-layer-id]')).toHaveCount(1);
  await expect(page.locator('#viewportEmpty')).toBeHidden();
  await page.locator('#controlTab-export').click();
  const pending = page.waitForEvent('download');
  await page.locator('#exportFrameButton').click();
  const download = await pending;
  const file = await download.path();
  expect(file).toBeTruthy();
  expect(readFileSync(file!).subarray(0, 8).toString('hex')).toBe('89504e470d0a1a0a');
});
