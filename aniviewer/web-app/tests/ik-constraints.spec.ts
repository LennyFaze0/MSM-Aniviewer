import { test, expect, type Page } from '@playwright/test';
import { fixture, inspector, select, center, colors, panel } from './helpers/sprite-editing';

async function setup(page: Page, one = false, expectedError?: string) {
  const joints = [];
  for (const id of [0, 1, 2]) { await select(page, id); joints.push(await center(page, id)); }
  await inspector(page);
  await page.locator('#ikSetup summary').first().click();
  await page.locator('#ikType').selectOption(one ? '1' : '2');
  await page.locator('#ikUpper').selectOption('0');
  if (!one) await page.locator('#ikLower').selectOption('1');
  for (const [key, index] of (one ? [['root', 0], ['tip', 1]] : [['root', 0], ['bend', 1], ['tip', 2]]) as [string, number][]) {
    await page.locator(`[data-ik-place="${key}"]`).click();
    await page.mouse.click(joints[index]!.x, joints[index]!.y);
    await expect(page.locator(`[data-ik-joint="${key}"]`)).toHaveCount(1);
  }
  await page.locator('[data-ik-command="create"]').click();
  if (expectedError) { await expect(page.locator('#ikError')).toContainText(expectedError); return; }
  await expect(page.locator('#ikError')).toBeEmpty();
  await expect(page.locator('[data-ik-target]')).toHaveCount(1);
  await expect(page.locator('#ikEnabled')).toBeChecked();
}
async function target(page: Page, x: string, y: string) {
  await inspector(page);
  for (const [id, value] of [['ikX', x], ['ikY', y]]) {
    await page.locator(`#${id}`).fill(value!); await page.locator(`#${id}`).dispatchEvent('change');
  }
}
async function scrub(page: Page, time: number) {
  await page.locator('#timeline').evaluate((node, value) => { (node as HTMLInputElement).value = String(value); node.dispatchEvent(new Event('input', { bubbles: true })); }, time);
}

test('explicit two-bone setup has no bind jump, reaches targets, exports the solve, and Off restores every sampled frame', async ({ page }) => {
  const errors: string[] = []; page.on('pageerror', (error) => errors.push(error.message));
  await fixture(page);
  const before = [];
  for (const time of [0, .37, .8]) { await scrub(page, time); before.push(await colors(page)); }
  await scrub(page, 0); await setup(page);
  expect(await colors(page)).toEqual(before[0]);
  await target(page, '-10', '5');
  await expect(page.locator('#ikStatus')).toContainText('IK active');
  const edited = await colors(page);
  expect(edited[0]).not.toEqual(before[0]![0]); expect(edited[1]).not.toEqual(before[0]![1]);
  expect(edited[2]).toEqual(before[0]![2]);
  await inspector(page); await page.locator('#ikEnabled').uncheck();
  for (const [index, time] of [0, .37, .8].entries()) {
    await scrub(page, time); expect(await colors(page)).toEqual(before[index]);
  }
  await scrub(page, 0); await page.locator('#transformDock [data-action="undo"]').click();
  expect(await colors(page)).toEqual(edited);
  expect(errors).toEqual([]);
});

test('one-bone aim supports world pinning, undo and unreachable target feedback without moving unrelated sprites', async ({ page }) => {
  await fixture(page); const original = await colors(page); await setup(page, true);
  await target(page, '-20', '15');
  const aimed = await colors(page); expect(aimed[0]).not.toEqual(original[0]);
  expect(aimed[1]).toEqual(original[1]); expect(aimed[2]).toEqual(original[2]);
  await inspector(page); await page.locator('#ikSpace').selectOption('world');
  expect(await colors(page)).toEqual(aimed);
  await inspector(page); const worldTarget = await page.locator('#ikX').inputValue();
  await scrub(page, .8); expect(await page.locator('#ikX').inputValue()).toBe(worldTarget);
  await target(page, '10000', '10000'); await expect(page.locator('#ikStatus')).toContainText('Out of reach');
  await page.locator('[data-ik-command="reset"]').click(); await scrub(page, 0);
  expect(await colors(page)).toEqual(original);
  await inspector(page); await page.locator('[data-ik-command="remove"]').click();
  await expect(page.locator('[data-ik-target]')).toHaveCount(0);
  await page.locator('#transformDock [data-action="undo"]').click();
  await expect(page.locator('[data-ik-target]')).toHaveCount(1);
});

test('target pointer gestures commit once, cancel safely, and cannot leak through disabled IK', async ({ page, isMobile, browserName }) => {
  await fixture(page); await setup(page);
  const handle = page.locator('[data-ik-target]'), before = await handle.getAttribute('cx');
  const rect = (await handle.boundingBox())!, x = rect.x + rect.width / 2, y = rect.y + rect.height / 2;
  if (isMobile && browserName === 'chromium') {
    const cdp = await page.context().newCDPSession(page);
    await cdp.send('Input.dispatchTouchEvent', { type: 'touchStart', touchPoints: [{ id: 1, x, y }] });
    for (let i = 1; i <= 6; i++) await cdp.send('Input.dispatchTouchEvent', { type: 'touchMove', touchPoints: [{ id: 1, x: x - i * 3, y: y + i }] });
    await cdp.send('Input.dispatchTouchEvent', { type: 'touchEnd', touchPoints: [] }); await cdp.detach();
  } else {
    await page.mouse.move(x, y); await page.mouse.down(); await page.mouse.move(x - 18, y + 6, { steps: 6 }); await page.mouse.up();
  }
  await expect.poll(() => page.locator('#ikX').inputValue()).not.toBe('0');
  const moved = await handle.getAttribute('cx'); expect(moved).not.toBe(before);
  await page.locator('#transformDock [data-action="undo"]').click();
  expect(await handle.getAttribute('cx')).toBe(before);
  await page.locator('#transformDock [data-action="redo"]').click();
  expect(await handle.getAttribute('cx')).toBe(moved);
  const next = (await handle.boundingBox())!;
  await page.mouse.move(next.x + 22, next.y + 22); await page.mouse.down(); await page.mouse.move(next.x + 12, next.y + 24, { steps: 4 });
  await page.keyboard.press('Escape'); await page.mouse.up();
  expect(await handle.getAttribute('cx')).toBe(moved);
  await inspector(page); await page.locator('#ikEnabled').uncheck();
  await expect(handle).toHaveCount(0);
  await panel(page, 'library'); await page.locator('#controlTab-animation').click();
  await page.locator('[data-track-index="1"]').click();
  await inspector(page); await expect(page.locator('#ikActive')).toHaveValue('');
});

test('incomplete setup and overlapping chains fail without changing an existing chain', async ({ page }) => {
  await fixture(page); await inspector(page); await page.locator('#ikSetup summary').first().click();
  await page.locator('[data-ik-command="create"]').click(); await expect(page.locator('#ikError')).toContainText('Place all joints');
  await page.locator('#ikSetup summary').first().click(); await setup(page);
  const before = await page.locator('[data-ik-target]').getAttribute('cx');
  // A second setup may inspect existing geometry, but cannot claim a bone twice.
  await page.locator('#ikSetup summary').first().click();
  await page.locator('[data-ik-place="root"]').click();
  await page.keyboard.press('Escape');
  await expect(page.locator('#ikStatus')).toContainText(/cancelled|Following original/);
  expect(await page.locator('[data-ik-target]').getAttribute('cx')).toBe(before);
  await page.locator('#ikSetup summary').first().click();
  await setup(page, false, 'two IK bones');
  expect(await page.locator('[data-ik-target]').getAttribute('cx')).toBe(before);
  expect(await page.locator('body').evaluate((node) => node.scrollWidth <= innerWidth + 1)).toBe(true);
});

test('touch second finger cancels target editing before camera gestures; placement accepts native taps', async ({ page, isMobile, browserName }) => {
  test.skip(!isMobile || browserName !== 'chromium', 'Native multitouch injection uses Chromium CDP.');
  await fixture(page); await select(page, 0); const p = await center(page, 0);
  await inspector(page); await page.locator('#ikSetup summary').first().click();
  await page.locator('[data-ik-place="root"]').tap(); await page.touchscreen.tap(p.x, p.y);
  await expect(page.locator('[data-ik-joint="root"]')).toHaveCount(1);
  await page.locator('#ikSetup summary').first().click(); await setup(page);
  const handle = page.locator('[data-ik-target]'), rect = (await handle.boundingBox())!, x = rect.x + 22, y = rect.y + 22;
  const cdp = await page.context().newCDPSession(page);
  await cdp.send('Input.dispatchTouchEvent', { type: 'touchStart', touchPoints: [{ id: 1, x, y }] });
  await cdp.send('Input.dispatchTouchEvent', { type: 'touchMove', touchPoints: [{ id: 1, x: x - 18, y: y + 4 }] });
  await expect.poll(() => page.locator('#ikX').inputValue()).not.toBe('0');
  await cdp.send('Input.dispatchTouchEvent', { type: 'touchStart', touchPoints: [{ id: 1, x: x - 18, y: y + 4 }, { id: 2, x: x - 70, y: y - 20 }] });
  await expect(page.locator('#ikX')).toHaveValue('0');
  await cdp.send('Input.dispatchTouchEvent', { type: 'touchEnd', touchPoints: [] }); await cdp.detach();
  // No target command was committed: Undo removes the chain itself.
  await page.locator('#transformDock [data-action="undo"]').tap(); await expect(handle).toHaveCount(0);
});
