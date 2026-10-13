import { expect, test } from '@playwright/test';

test('visible manual opens separately, searches, deep-links and fits phone screens', async ({ page, context }) => {
  const errors: string[] = [];
  await page.goto('.');
  const link = page.locator('#manualButton');
  await expect(link).toBeVisible();
  await expect(link).toHaveAttribute('target', '_blank');
  const newPage = context.waitForEvent('page');
  await link.click();
  const manual = await newPage;
  manual.on('pageerror', (error) => errors.push(error.message));
  await manual.waitForLoadState('domcontentloaded');
  await expect(manual).toHaveURL(/\/MSM-Aniviewer\/manual\.html$/);
  await expect(manual.getByRole('heading', { name: 'Viewer Manual', exact: true })).toBeVisible();
  expect(await manual.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth)).toBe(true);
  await manual.locator('#manualSearch').fill('frame rate');
  await expect(manual.locator('#export')).toBeVisible();
  await expect(manual.locator('#quick-start')).toBeHidden();
  await manual.locator('#manualSearch').fill('nonexistentword123');
  await expect(manual.locator('#noResults')).toBeVisible();
  await manual.evaluate(() => { location.hash = '#ik'; });
  await expect(manual.locator('#ik')).toBeVisible();
  await expect(manual.locator('#manualSearch')).toHaveValue('');
  const brokenLinks = await manual.evaluate(() => [...document.querySelectorAll<HTMLAnchorElement>('a[href^="#"]')]
    .filter((entry) => !document.getElementById(entry.hash.slice(1))).map((entry) => entry.hash));
  expect(brokenLinks).toEqual([]);
  expect(errors).toEqual([]);
  // Reading instructions never navigates or reloads the active viewer session.
  await expect(page.locator('.viewport-canvas')).toBeVisible();
  await manual.close();
});

test('manual remains readable without JavaScript', async ({ browser, baseURL }) => {
  const context = await browser.newContext({ javaScriptEnabled: false, viewport: { width: 320, height: 700 } });
  try {
    const page = await context.newPage();
    await page.goto(new URL('manual.html', baseURL).href);
    await expect(page.locator('[data-topic]:visible')).toHaveCount(13);
    await expect(page.getByRole('heading', { name: 'Export & save', exact: true })).toBeVisible();
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth)).toBe(true);
  } finally { await context.close(); }
});
