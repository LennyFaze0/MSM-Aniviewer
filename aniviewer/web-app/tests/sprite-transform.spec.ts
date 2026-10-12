import { test, expect } from '@playwright/test';
import { fixture, panel, select, inspector, field, outline, center, colors, chooseTool } from './helpers/sprite-editing';

test('numeric parent edits appear in exports without moving children or grandchildren; undo/redo is exact', async ({ page }) => {
  await fixture(page); await select(page, 0);
  const original = await colors(page);
  expect(original.every((pixels) => pixels.length > 100)).toBe(true);
  await field(page, 'offsetX', '-10'); await field(page, 'offsetY', '-11');
  await field(page, 'rotation', '27'); await field(page, 'scaleX', '130'); await field(page, 'scaleY', '80');
  const edited = await colors(page);
  expect(edited[0]).not.toEqual(original[0]);
  expect(edited[1]).toEqual(original[1]); expect(edited[2]).toEqual(original[2]);
  for (let i = 0; i < 5; i++) await page.locator('#transformDock [data-action="undo"]').click();
  await expect(page.locator('#transformDock [data-action="undo"]')).toBeDisabled();
  expect(await colors(page)).toEqual(original);
  for (let i = 0; i < 5; i++) await page.locator('#transformDock [data-action="redo"]').click();
  expect(await colors(page)).toEqual(edited);
});

test('dragging, snapped rotation, scale, cancel and viewport selection use the rendered pose', async ({ page }) => {
  await fixture(page); await select(page, 0);
  const original = await outline(page, 0);
  await chooseTool(page, 'move');
  const start = await center(page, 0);
  await page.mouse.move(start.x, start.y); await page.mouse.down();
  await page.mouse.move(start.x - 25, start.y - 10, { steps: 8 }); await page.mouse.up();
  await inspector(page);
  // Compare to the displayed sprite, including fractional mobile layout/UI scaling.
  expect(Number(await page.locator('[data-transform-field="offsetX"]').inputValue())).toBeCloseTo(-25 * 16 / start.width, 2);
  expect(Number(await page.locator('[data-transform-field="offsetY"]').inputValue())).toBeCloseTo(-10 * 16 / start.height, 2);
  await page.locator('#transformDock [data-action="undo"]').click();
  expect(await outline(page, 0)).toBe(original);
  await chooseTool(page, 'rotate');
  await page.locator('#transformDock [data-action="snap"]').click();
  const current = await center(page, 0);
  await page.mouse.move(current.x, current.y); await page.mouse.down();
  await page.mouse.move(current.x + 50, current.y, { steps: 8 }); await page.mouse.up();
  await expect(page.locator('[data-transform-field="rotation"]')).toHaveValue('30');
  await chooseTool(page, 'scale');
  await page.locator('#transformScaleAxis').selectOption('x');
  await page.locator('[data-action="grow"]').click();
  await expect(page.locator('[data-transform-field="scaleX"]')).toHaveValue('105');
  await expect(page.locator('[data-transform-field="scaleY"]')).toHaveValue('100');
  const beforeCancel = await outline(page, 0);
  await chooseTool(page, 'move');
  const point = await center(page, 0);
  await page.mouse.move(point.x, point.y); await page.mouse.down();
  await page.mouse.move(point.x - 35, point.y, { steps: 6 });
  await page.keyboard.press('Escape'); await page.mouse.up();
  expect(await outline(page, 0)).toBe(beforeCancel);
  await chooseTool(page, 'select');
  await page.locator('#transformDock [data-action="clear"]').click();
  await page.mouse.click(point.x, point.y);
  await expect(page.locator('[data-layer-id="0"]')).toHaveAttribute('aria-selected', 'true');
});

test('Multi edits are atomic, camera pans do not edit, and hidden layers cannot be picked', async ({ page }) => {
  await fixture(page); await select(page, 0);
  await page.locator('[data-action="multi"]').click(); await select(page, 1);
  await page.locator('[data-action="multi"]').click();
  const before = [await outline(page, 0), await outline(page, 1)];
  await chooseTool(page, 'move'); await page.locator('[data-action="right"]').click();
  expect(await outline(page, 0)).not.toBe(before[0]); expect(await outline(page, 1)).not.toBe(before[1]);
  await page.locator('#transformDock [data-action="undo"]').click();
  expect(await outline(page, 0)).toBe(before[0]); expect(await outline(page, 1)).toBe(before[1]);
  await expect(page.locator('#transformDock [data-action="undo"]')).toBeDisabled();
  await chooseTool(page, 'pan');
  const point = await center(page, 0);
  await page.mouse.move(point.x, point.y); await page.mouse.down(); await page.mouse.move(point.x + 20, point.y + 10); await page.mouse.up();
  expect(await outline(page, 0)).not.toBe(before[0]);
  await expect(page.locator('#transformDock [data-action="undo"]')).toBeDisabled();
  await page.locator('[data-action="clear"]').click(); await select(page, 0);
  const hiddenPoint = await center(page, 0);
  await page.locator('[data-layer-id="0"] .visibility-button').click();
  await expect(page.locator('[data-selection-outline="0"]')).toHaveCount(0);
  await page.locator('[data-action="clear"]').click(); await chooseTool(page, 'select');
  await page.mouse.click(hiddenPoint.x, hiddenPoint.y);
  await expect(page.locator('#viewportPanel')).toHaveAttribute('data-selected-layers', '');
});

test('touch selection, step controls and two-finger interruption never leave a partial edit', async ({ page, browserName, isMobile }) => {
  test.skip(browserName !== 'chromium' || !isMobile, 'Trusted multi-touch replay uses Chromium CDP on the mobile profile.');
  await fixture(page); await select(page, 0);
  const start = await center(page, 0);
  await page.locator('[data-action="clear"]').tap();
  await page.touchscreen.tap(start.x, start.y);
  await expect(page.locator('[data-layer-id="0"]')).toHaveAttribute('aria-selected', 'true');
  await chooseTool(page, 'move');
  await page.locator('[data-action="up"]').tap();
  await inspector(page); await expect(page.locator('[data-transform-field="offsetY"]')).toHaveValue('-1');
  await page.locator('#transformDock [data-action="undo"]').tap();
  const p = await center(page, 0);
  const cdp = await page.context().newCDPSession(page);
  const touch = (type: string, points: Array<{ id: number; x: number; y: number }>) => cdp.send('Input.dispatchTouchEvent', { type, touchPoints: points });
  await touch('touchStart', [{ id: 1, x: p.x, y: p.y }]);
  await touch('touchMove', [{ id: 1, x: p.x - 20, y: p.y }]);
  await expect(page.locator('[data-transform-field="offsetX"]')).not.toHaveValue('0');
  await touch('touchStart', [{ id: 1, x: p.x - 20, y: p.y }, { id: 2, x: p.x + 30, y: p.y }]);
  await touch('touchMove', [{ id: 1, x: p.x - 35, y: p.y }, { id: 2, x: p.x + 50, y: p.y }]);
  await touch('touchEnd', []);
  await expect(page.locator('[data-transform-field="offsetX"]')).toHaveValue('0');
  await expect(page.locator('#transformDock [data-action="undo"]')).toBeDisabled();
  const afterCamera = await center(page, 0);
  expect(afterCamera.width).toBeGreaterThan(p.width);
  await touch('touchStart', [{ id: 1, x: afterCamera.x, y: afterCamera.y }]);
  await touch('touchMove', [{ id: 1, x: afterCamera.x - 25, y: afterCamera.y }]);
  await touch('touchEnd', []);
  expect(Number(await page.locator('[data-transform-field="offsetX"]').inputValue())).toBeLessThan(-1);
  await page.locator('#transformDock [data-action="undo"]').tap();
  await expect(page.locator('[data-transform-field="offsetX"]')).toHaveValue('0');
  await expect(page.locator('#transformDock [data-action="undo"]')).toBeDisabled();
  await cdp.detach();
});

test('rotation handle, flips and keyboard shortcuts work without stealing numeric input keys', async ({ page }) => {
  await fixture(page); await select(page, 0); await inspector(page);
  await page.locator('#viewportPanel').focus(); await page.keyboard.press('e');
  await expect(page.locator('#transformDock [data-transform-tool="rotate"]')).toHaveAttribute('aria-pressed', 'true');
  const pivot = await page.locator('.transform-pivot').evaluate((node) => {
    const b = node.getBoundingClientRect(); return { x: b.x + b.width / 2, y: b.y + b.height / 2 };
  });
  const handle = await page.locator('[data-transform-handle="rotate"]').evaluate((node) => {
    const b = node.getBoundingClientRect(); return { x: b.x + b.width / 2, y: b.y + b.height / 2 };
  });
  await page.locator('#viewportPanel').evaluate((host) => {
    // WebKit rounds dispatched pointer coordinates. Test free rotation against the
    // delivered coordinates in canvas space, not an unattainable subpixel mouse target.
    for (const type of ['pointerdown', 'pointerup']) host.addEventListener(type, (raw) => {
      const event = raw as PointerEvent;
      const canvas = host.querySelector('canvas')!, rect = canvas.getBoundingClientRect();
      const pivot = host.querySelector('.transform-pivot')!;
      const x = (event.clientX - rect.left) * host.clientWidth / rect.width - Number(pivot.getAttribute('cx'));
      const y = (event.clientY - rect.top) * host.clientHeight / rect.height - Number(pivot.getAttribute('cy'));
      host.setAttribute(`data-test-${type}-angle`, String(Math.atan2(y, x) * 180 / Math.PI));
    }, { capture: true });
  });
  await page.mouse.move(handle.x, handle.y); await page.mouse.down();
  await page.mouse.move(pivot.x + pivot.y - handle.y, pivot.y + handle.x - pivot.x, { steps: 10 }); await page.mouse.up();
  const expected = await page.locator('#viewportPanel').evaluate((host) => {
    const start = Number(host.getAttribute('data-test-pointerdown-angle')), end = Number(host.getAttribute('data-test-pointerup-angle'));
    return ((end - start + 540) % 360 + 360) % 360 - 180;
  });
  expect(Number(await page.locator('[data-transform-field="rotation"]').inputValue())).toBeCloseTo(expected, 3);
  await page.locator('#transformDock [data-action="undo"]').click();
  await page.locator('#transformDock [data-action="snap"]').click();
  await page.mouse.move(handle.x, handle.y); await page.mouse.down();
  await page.mouse.move(pivot.x + pivot.y - handle.y, pivot.y + handle.x - pivot.x, { steps: 10 }); await page.mouse.up();
  expect(Number(await page.locator('[data-transform-field="rotation"]').inputValue())).toBeCloseTo(90, 1);
  await page.locator('#transformDock [data-action="snap"]').click();
  await page.locator('[data-action="flip-x"]').click();
  await expect(page.locator('[data-transform-field="scaleX"]')).toHaveValue('-100');
  await page.locator('#viewportPanel').focus(); await page.keyboard.press('Control+z');
  await expect(page.locator('[data-transform-field="scaleX"]')).toHaveValue('100');
  await page.keyboard.press('Control+Shift+z');
  await expect(page.locator('[data-transform-field="scaleX"]')).toHaveValue('-100');
  await page.keyboard.press('v'); await page.keyboard.press('Shift+ArrowRight');
  await expect(page.locator('[data-transform-field="offsetX"]')).toHaveValue('10');
  await page.locator('[data-transform-field="offsetX"]').focus(); await page.keyboard.press('e');
  await expect(page.locator('#transformDock [data-transform-tool="move"]')).toHaveAttribute('aria-pressed', 'true');
});

test('dragging stays aligned under UI scaling and viewport resizing', async ({ page }) => {
  await fixture(page); await select(page, 0);
  await page.evaluate(() => {
    document.documentElement.style.setProperty('--ui-scale', '1.25');
    document.documentElement.style.setProperty('--ui-scale-inverse', '.8');
  });
  await page.locator('#fitButton').click();
  await chooseTool(page, 'move');
  const start = await center(page, 0);
  await page.mouse.move(start.x, start.y); await page.mouse.down();
  await page.mouse.move(start.x - 20, start.y - 10, { steps: 8 }); await page.mouse.up();
  const after = await center(page, 0);
  expect(after.x - start.x).toBeCloseTo(-20, 1);
  expect(after.y - start.y).toBeCloseTo(-10, 1);
  await page.locator('#transformDock [data-action="undo"]').click();
  const reset = await center(page, 0);
  expect(reset.x).toBeCloseTo(start.x, 4); expect(reset.y).toBeCloseTo(start.y, 4);
});

test('controls fit the viewport and switching animations clears transient edits', async ({ page }, info) => {
  await fixture(page); await select(page, 0); await inspector(page);
  await chooseTool(page, 'scale');
  await page.locator('[data-action="grow"]').click();
  const overflow = await page.locator('#transformDock').evaluate((dock) => ({
    width: dock.clientWidth, scroll: dock.scrollWidth,
    buttons: [...dock.querySelectorAll('button')].filter((button) => button.getBoundingClientRect().height > 0).map((button) => ({ width: button.getBoundingClientRect().width, height: button.getBoundingClientRect().height })),
  }));
  expect(overflow.scroll).toBeLessThanOrEqual(overflow.width + 1);
  if (info.project.name.includes('mobile')) expect(overflow.buttons.every((button) => button.width >= 44 && button.height >= 44)).toBe(true);
  await page.screenshot({ path: info.outputPath('sprite-controls.png') });
  await page.locator('#controlTab-animation').click();
  await page.locator('[data-track-index="1"]').click();
  await expect(page.locator('#transformDock [data-action="undo"]')).toBeDisabled();
  await expect(page.locator('[data-selection-outline]')).toHaveCount(0);
  await select(page, 0); await inspector(page);
  await expect(page.locator('[data-transform-field="scaleX"]')).toHaveValue('100');
});
