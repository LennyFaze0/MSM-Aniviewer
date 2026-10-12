import { expect, test } from '@playwright/test';
import { BlobWriter, TextReader, Uint8ArrayReader, ZipWriter } from '@zip.js/zip.js';
import { nativeRev6Fixture } from './native-fixture';

test('opens Monster Browser from File Selection with an accessible empty state', async ({ page }) => {
  await page.goto('.');
  const opener = page.locator('#controlPanel-files .file-actions #monsterBrowserButton');
  await opener.click();
  const browser = page.getByRole('dialog', { name: 'Monster Browser' });
  await expect(browser).toBeVisible();
  await expect(browser.getByRole('searchbox')).toBeFocused();
  await expect(browser).toContainText('No animations imported yet');
  await expect(browser.getByRole('button', { name: 'Load Monster' })).toBeDisabled();
  const bounds = await browser.evaluate((dialog) => ({
    width: dialog.scrollWidth, client: dialog.clientWidth, bottom: dialog.getBoundingClientRect().bottom, screen: innerHeight,
  }));
  expect(bounds.width).toBeLessThanOrEqual(bounds.client);
  expect(bounds.bottom).toBeLessThanOrEqual(bounds.screen);
  await page.keyboard.press('Escape');
  await expect(browser).not.toBeVisible();
  await expect(opener).toBeFocused();
  await opener.click();
  const chooser = page.waitForEvent('filechooser');
  await browser.getByRole('button', { name: 'Open Animation Files' }).click();
  expect((await chooser).isMultiple()).toBe(true);
  await expect(browser).not.toBeVisible();
});

test('browses ZIP portraits, filters by monster name, and loads the chosen duplicate source', async ({ page }) => {
  const fixture = nativeRev6Fixture();
  const zip = new ZipWriter(new BlobWriter('application/zip'));
  for (const install of ['game1', 'game2']) {
    const bin = Buffer.from(fixture.bin);
    if (install === 'game2') bin.write('Wave', bin.indexOf('Idle'), 'utf8');
    await zip.add(`${install}/data/monster_a.bin`, new Uint8ArrayReader(bin));
    await zip.add(`${install}/data/atlas.xml`, new TextReader(fixture.xml));
    await zip.add(`${install}/data/atlas.png`, new Uint8ArrayReader(fixture.png));
    await zip.add(`${install}/gfx/book/monster_portrait_square_a.png`, new Uint8ArrayReader(fixture.png));
  }
  await zip.add('game1/data/monster_e.bin', new Uint8ArrayReader(fixture.bin));
  await zip.add('game1/data/monster_a_rare.bin', new Uint8ArrayReader(fixture.bin));
  // Corrupt art must fall back to a readable card, not hide the monster.
  await zip.add('game1/gfx/book/monster_portrait_square_e.png', new TextReader('not a PNG'));
  const archive = await zip.close();
  await page.goto('.');
  await page.locator('#fileInput').setInputFiles({ name: 'monsters.zip', mimeType: 'application/zip', buffer: Buffer.from(await archive.arrayBuffer()) });
  await expect(page.locator('#statusText')).toHaveText('1 animation loaded from BIN');
  await page.locator('#controlTab-files').click();
  await page.locator('#monsterBrowserButton').click();
  const browser = page.getByRole('dialog', { name: 'Monster Browser' });
  await expect(browser.locator('.monster-browser-card')).toHaveCount(3);
  const tweedle = browser.getByRole('button', { name: 'Tweedle', exact: true });
  await expect(tweedle.locator('img')).toBeVisible();
  await browser.getByRole('searchbox').fill('mammott');
  await expect(browser.locator('.monster-browser-card')).toHaveCount(1);
  await browser.getByRole('button', { name: 'Mammott', exact: true }).click();
  await expect(browser.getByRole('button', { name: 'Load Monster' })).toBeEnabled();
  await browser.getByRole('searchbox').fill('rare tweedle');
  await expect(browser.locator('.monster-browser-card')).toHaveCount(1);
  await expect(browser.getByRole('button', { name: 'Rare Tweedle', exact: true })).toBeVisible();
  await browser.locator('[data-category]').selectOption('DOF');
  await expect(browser).toContainText('No matches.');
  await expect(browser.getByRole('button', { name: 'Load Monster' })).toBeDisabled();
  await browser.locator('[data-category]').selectOption('MSM');
  await browser.getByRole('searchbox').fill('game2');
  await expect(browser.locator('.monster-browser-card')).toHaveCount(1);
  await tweedle.click();
  const source = browser.getByRole('combobox', { name: 'Monster animation source' });
  await expect(source.locator('option')).toHaveCount(2);
  await source.selectOption('monsters.zip/game2/data/monster_a.bin');
  await expect(tweedle.locator('img')).toBeVisible();
  await tweedle.click();
  await expect(source).toHaveValue('monsters.zip/game2/data/monster_a.bin');
  const selectedUrl = await tweedle.locator('img').getAttribute('src');
  await browser.getByRole('button', { name: 'Load Monster' }).click();
  await expect(browser).not.toBeVisible();
  await expect(page.locator('#statusText')).toHaveText('1 animation loaded from BIN');
  await expect(page.locator('#viewportEmpty')).toBeHidden();
  await expect(page.locator('#animationFileSelect option:checked')).toHaveText('[BIN] monster_a.bin');
  await expect(page.locator('#trackList [data-track-index]')).toHaveCount(1);
  await expect(page.locator('#trackList .row-name')).toHaveText('Wave');
  expect(await page.evaluate(async (url) => {
    try { await fetch(url!); return false; } catch { return true; }
  }, selectedUrl)).toBe(true);
  await page.locator('#controlTab-files').click();
  await page.locator('#monsterBrowserButton').click();
  await expect(source).toHaveValue('monsters.zip/game2/data/monster_a.bin');
});

test('paginates large libraries without rendering every monster at once', async ({ page }) => {
  const fixture = nativeRev6Fixture();
  await page.goto('.');
  await page.locator('#fileInput').setInputFiles([
    ...Array.from({ length: 55 }, (_, index) => ({
      name: `monster_custom${String(index).padStart(2, '0')}.bin`, mimeType: 'application/octet-stream', buffer: Buffer.from(fixture.bin),
    })),
    { name: 'atlas.xml', mimeType: 'application/xml', buffer: Buffer.from(fixture.xml) },
    { name: 'atlas.png', mimeType: 'image/png', buffer: fixture.png },
  ]);
  await expect(page.locator('#statusText')).toHaveText('1 animation loaded from BIN');
  await page.locator('#controlTab-files').click();
  await page.locator('#monsterBrowserButton').click();
  const browser = page.getByRole('dialog', { name: 'Monster Browser' });
  await expect(browser.locator('.monster-browser-card')).toHaveCount(48);
  await browser.getByRole('button', { name: 'Next monster page' }).click();
  await expect(browser.locator('.monster-browser-card')).toHaveCount(7);
  await expect(browser.getByRole('button', { name: 'Next monster page' })).toBeDisabled();
  await browser.getByRole('searchbox').fill('custom02');
  await expect(browser.locator('.monster-browser-card')).toHaveCount(1);
  await expect(browser).toContainText('Page 1 of 1');
});
