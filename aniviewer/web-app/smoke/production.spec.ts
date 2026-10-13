import { expect, test } from '@playwright/test';
import { readFileSync } from 'node:fs';
import { nativeRev6Fixture } from '../tests/native-fixture';

// Reuse real-sample audio checks without dev-server-only module imports.
import '../tests/buffered-audio-playback.spec';

test('home-screen metadata uses valid iOS and Android viewer icons under the deployed base', async ({ page, request }) => {
  await page.goto('.');
  const manifestURL = await page.locator('link[rel="manifest"]').evaluate((link: HTMLLinkElement) => link.href);
  const response = await request.get(manifestURL);
  expect(response.ok()).toBe(true);
  const manifest = await response.json();
  const root = new URL('./', page.url()).href;
  expect(new URL(manifest.id, manifestURL).href).toBe(root);
  expect(new URL(manifest.start_url, manifestURL).href).toBe(root);
  expect(new URL(manifest.scope, manifestURL).href).toBe(root);
  expect(manifest.display).toBe('standalone');
  expect(manifest.icons.map((icon: { sizes: string; purpose: string }) => [icon.sizes, icon.purpose]))
    .toEqual([['192x192', 'any'], ['512x512', 'any'], ['512x512', 'maskable']]);
  const apple = page.locator('link[rel="apple-touch-icon"]');
  await expect(apple).toHaveAttribute('sizes', '180x180');
  const appleURL = await apple.evaluate((link: HTMLLinkElement) => link.href);
  const icons = [...manifest.icons.map((icon: { src: string; sizes: string }) => ({
    url: new URL(icon.src, manifestURL).href, size: Number(icon.sizes.split('x')[0]),
  })), { url: appleURL, size: 180 }];
  for (const icon of icons) {
    expect(icon.url.startsWith(`${root}icons/`)).toBe(true);
    const image = await request.get(icon.url);
    expect(image.ok()).toBe(true);
    expect(image.headers()['content-type']).toContain('image/png');
    const bytes = await image.body();
    expect(bytes.subarray(0, 8).toString('hex')).toBe('89504e470d0a1a0a');
    expect([bytes.readUInt32BE(16), bytes.readUInt32BE(20)]).toEqual([icon.size, icon.size]);
  }
});

test('published shell, settings, assets and decoder load on desktop and phones', async ({ page }) => {
  const errors: string[] = [];
  page.on('pageerror', (error) => errors.push(error.message));
  page.on('response', (response) => {
    if (response.status() >= 400) errors.push(`${response.status()} ${response.url()}`);
  });
  await page.goto('.');
  await expect(page.locator('.viewport-canvas')).toBeVisible();
  await expect(page.locator('#importFilesButton')).toBeVisible();
  await expect(page.locator('#manualButton, a[href$="manual.html"]')).toHaveCount(0);
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
