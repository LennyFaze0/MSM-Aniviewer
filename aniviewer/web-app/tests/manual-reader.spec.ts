import { expect, test, type Page } from '@playwright/test';
import { readFileSync } from 'node:fs';
import { field, fixture, outline, select } from './helpers/sprite-editing';

const source = readFileSync(new URL('../src/content/viewer-manual.txt', import.meta.url), 'utf8');
const separators = new Set(['0o0o0o0o0o0o0o0o0o0o0o0o0o0o0o0o0o0o0o0o0o', 'XXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXX', '------------------------------------']);
const expected = source.split('\n').filter((line) => !separators.has(line)).join('\n');

async function open(page: Page) {
  await page.locator('#manualButton').click();
  await expect(page.locator('#manualReader')).toBeVisible();
  await expect.poll(() => page.locator('#manualReader').evaluate((node) => node.getAnimations().length)).toBe(0);
}

async function jump(page: Page, id: string) {
  if (await page.locator('#manualSectionSelect').isVisible()) await page.locator('#manualSectionSelect').selectOption(id);
  else await page.locator(`[data-manual-target="${id}"]`).click();
}

test('manual preserves the author text, presents sections and opens without loading assets', async ({ page }, info) => {
  const errors: string[] = [];
  page.on('pageerror', (error) => errors.push(error.message));
  await page.goto('.');
  const url = page.url();
  await open(page);
  const article = page.locator('#manualDocument');
  expect(await article.textContent()).toBe(expected);
  await expect(article.locator('.manual-card')).toHaveCount(9);
  await expect(article.locator('h1')).toHaveText(source.split('\n')[0]!);
  await expect(article.locator('h2')).toHaveCount(8);
  await expect(article.locator('iframe, pre')).toHaveCount(0);
  expect(await page.locator('#manualReader').evaluate((node) => node.matches(':modal'))).toBe(true);
  await expect(page.locator('#manualButton')).toHaveAttribute('aria-expanded', 'true');
  for (const link of await article.locator('a[target="_blank"]').all()) {
    expect(await link.getAttribute('href')).toBe(await link.textContent());
    await expect(link).toHaveAttribute('rel', 'noopener noreferrer');
  }
  await page.screenshot({ path: info.outputPath('manual-introduction.png') });
  await jump(page, 'manual-section-5');
  await expect.poll(() => article.evaluate((node) => node.scrollTop)).toBeGreaterThan(0);
  expect(await article.textContent()).toBe(expected);
  await page.screenshot({ path: info.outputPath('manual-export-section.png') });
  await page.getByRole('button', { name: 'Close manual', exact: true }).click();
  await expect(page.locator('#manualReader')).toBeHidden();
  await expect(page.locator('#manualButton')).toBeFocused();
  expect(page.url()).toBe(url);
  expect(errors).toEqual([]);
});

test('manual search highlights and cycles literal matches without changing or hiding any text', async ({ page }) => {
  await page.goto('.');
  await open(page);
  const search = page.locator('#manualSearch');
  const article = page.locator('#manualDocument');
  await search.fill('MANIFEST');
  const matches = expected.toLowerCase().split('manifest').length - 1;
  await expect(page.locator('#manualSearchCount')).toHaveText(`1 / ${matches}`);
  expect(await article.textContent()).toBe(expected);
  await page.getByRole('button', { name: 'Next match', exact: true }).click();
  await expect(page.locator('#manualSearchCount')).toHaveText(`2 / ${matches}`);
  await search.press('Shift+Enter');
  await expect(page.locator('#manualSearchCount')).toHaveText(`1 / ${matches}`);
  await page.getByRole('button', { name: 'Previous match', exact: true }).click();
  await expect(page.locator('#manualSearchCount')).toHaveText(`${matches} / ${matches}`);
  // Match spans both the emphasized label and its following body text.
  await search.fill('Load Manifest - use this');
  await expect(article.locator('.manual-current-match')).toHaveCount(2);
  expect(await article.textContent()).toBe(expected);
  await search.fill('no such manual phrase');
  await expect(page.locator('#manualSearchCount')).toHaveText('0 / 0');
  await expect(page.getByRole('button', { name: 'Next match', exact: true })).toBeDisabled();
  await expect(article.locator('.manual-card')).toHaveCount(9);
  await search.fill('');
  await expect(article.locator('mark')).toHaveCount(0);
  expect(await article.textContent()).toBe(expected);
});

test('reader traps focus and editing shortcuts without changing the loaded pose or history', async ({ page }) => {
  await fixture(page);
  await select(page, 0);
  await field(page, 'offsetX', '12');
  const before = await outline(page, 0);
  await open(page);
  await expect(page.getByRole('button', { name: 'Close manual', exact: true })).toBeFocused();
  await page.keyboard.press('Shift+Tab');
  expect(await page.evaluate(() => document.activeElement?.closest('#manualReader') !== null)).toBe(true);
  await page.keyboard.press('Tab');
  await expect(page.getByRole('button', { name: 'Close manual', exact: true })).toBeFocused();
  await page.locator('#manualDocument').focus();
  for (const key of ['Space', 'r', 'v', 'Control+z', 'Control+Shift+z']) await page.keyboard.press(key);
  expect(await outline(page, 0)).toBe(before);
  await expect(page.locator('#playButton')).toHaveAttribute('aria-label', 'Play');
  await page.keyboard.press('Escape');
  await expect(page.locator('#manualReader')).toBeHidden();
  await expect(page.locator('#manualButton')).toBeFocused();
  await expect(page.locator('[data-transform-field="offsetX"]')).toHaveValue('12');
  expect(await outline(page, 0)).toBe(before);
  await page.locator('#transformDock [data-action="undo"]').click();
  await expect(page.locator('[data-transform-field="offsetX"]')).toHaveValue('0');
});

test('reader stays usable with large UI text, phone landscape and reduced motion', async ({ page }, info) => {
  await page.emulateMedia({ reducedMotion: 'reduce', colorScheme: 'light' });
  await page.addInitScript(() => localStorage.setItem('aniviewer.viewerSettings.v1', JSON.stringify({ interface: { scale: 1.25, themeMode: 'light' } })));
  await page.goto('.');
  await open(page);
  await jump(page, 'manual-section-6');
  const scroll = await page.locator('#manualDocument').evaluate((node) => node.scrollTop);
  await page.keyboard.press('Escape');
  await expect(page.locator('#manualReader')).toBeHidden();
  await open(page);
  expect(await page.locator('#manualDocument').evaluate((node) => node.scrollTop)).toBeCloseTo(scroll, 0);
  await page.setViewportSize({ width: 667, height: 375 });
  await expect.poll(() => page.locator('#manualReader').evaluate((node) => node.getBoundingClientRect().bottom)).toBeLessThanOrEqual(375);
  await expect(page.locator('#manualSectionSelect')).toBeVisible();
  await jump(page, 'manual-section-6');
  const dimensions = await page.locator('#manualReader').evaluate((node) => {
    const article = node.querySelector('#manualDocument')!;
    const rect = node.getBoundingClientRect();
    return { width: rect.width, right: rect.right, height: rect.height, bottom: rect.bottom,
      overflow: node.scrollWidth > node.clientWidth, articleOverflow: article.scrollWidth > article.clientWidth,
      readableHeight: article.clientHeight };
  });
  expect(dimensions.width).toBeLessThanOrEqual(667);
  expect(dimensions.right).toBeLessThanOrEqual(667);
  expect(dimensions.bottom).toBeLessThanOrEqual(375);
  expect(dimensions.overflow).toBe(false);
  expect(dimensions.articleOverflow).toBe(false);
  expect(dimensions.readableHeight).toBeGreaterThan(100);
  expect(await page.locator('#manualDocument').textContent()).toBe(expected);
  await page.screenshot({ path: info.outputPath('manual-landscape-light.png') });
});

test('slide-up reader can dismiss and reopen in the dark theme without losing reading position', async ({ page }, info) => {
  await page.emulateMedia({ colorScheme: 'dark' });
  await page.goto('.');
  await open(page);
  await jump(page, 'manual-section-5');
  const article = page.locator('#manualDocument');
  const scroll = await article.evaluate((node) => node.scrollTop);
  await page.screenshot({ path: info.outputPath('manual-dark.png') });
  // The backdrop is outside the sheet even on full-width phones.
  await page.mouse.click(1, 1);
  await expect(page.locator('#manualReader')).toBeHidden();
  await expect(page.locator('#manualButton')).toHaveAttribute('aria-expanded', 'false');
  await open(page);
  expect(await article.evaluate((node) => node.scrollTop)).toBeCloseTo(scroll, 0);
  expect(await article.textContent()).toBe(expected);
  await expect(page.locator('#settingsLayer')).toBeHidden();
});

test('file examples replace the placeholders and link directly to detailed BIN and XML sections', async ({ page }) => {
  await page.goto('.');
  await open(page);
  const article = page.locator('#manualDocument');
  await expect(article).not.toContainText('see -------');
  const links = page.locator('#manual-section-1 [data-manual-reference]');
  await expect(links).toHaveCount(3);
  const url = page.url();
  for (const index of [0, 1, 2]) {
    await jump(page, 'manual-section-1');
    const link = links.nth(index);
    const target = await link.getAttribute('data-manual-reference');
    await link.click();
    const distance = await page.locator(`#${target}`).evaluate((node) => node.getBoundingClientRect().top - document.querySelector('#manualDocument')!.getBoundingClientRect().top);
    expect(Math.abs(distance - 16)).toBeLessThan(2);
    expect(page.url()).toBe(url);
  }
  const bin = page.locator('#manual-section-7');
  for (const example of ['001_B.bin', '001_kermit.bin', 'monster_a_RARE.bin', 'muppet_kermit_stage01.bin', 'costume_A_09.bin', 'bakeries.bin', 'Rev 1 (Legacy Mobile)', 'Rev 2 (My Muppets Show)', 'Rev 3 (Monster Choir)', 'Rev 6']) await expect(bin).toContainText(example);
  const xml = page.locator('#manual-section-8');
  for (const example of ['monster_A_sheet.xml', 'kermit_sheet.xml', 'monster_U05_RARE_sheet.xml', 'monster_A_costume_09_sheet.xml', '<TextureAtlas imagePath=']) await expect(xml).toContainText(example);
  // XML examples remain literal text, never live elements parsed as HTML.
  await expect(xml.locator('TextureAtlas, sprite')).toHaveCount(0);
  expect(await article.textContent()).toBe(expected);
});
