import { test, expect, type Locator, type Page } from '@playwright/test';
import { fixture, select, center, colors, inspector, outline } from './helpers/sprite-editing';

async function activate(button: Locator, touch: boolean) { if (touch) await button.tap(); else await button.click(); }
async function open(page: Page, touch: boolean) {
  await activate(page.locator('#toolModeButton'), touch);
  await expect(page.locator('#toolModePicker')).toBeVisible();
  // Wait for the expansion to finish before measuring its final position.
  await page.locator('#toolModePicker').evaluate(async (node) => { await Promise.all(node.getAnimations().map((animation) => animation.finished.catch(() => {}))); });
}

test('Tools opens a static grid; choosing any mode retracts without changing selection, pixels or history', async ({ page, isMobile }, info) => {
  await fixture(page); await select(page, 0);
  const pixels = await colors(page), selected = await outline(page, 0);
  const menu = page.locator('#toolModePicker'), button = page.locator('#toolModeButton');
  for (const mode of ['move', 'rotate', 'scale', 'pan', 'select']) {
    await open(page, isMobile);
    await expect(button).toHaveAttribute('aria-expanded', 'true');
    await expect(menu.locator('[data-picker-mode]')).toHaveCount(5);
    const labels = await menu.locator('strong').allTextContents();
    expect(labels).toEqual(['Select', 'Move', 'Rotate', 'Scale', 'Pan', 'Cancel']);
    if (mode === 'move') await page.screenshot({ path: info.outputPath('tool-popout.png') });
    await activate(menu.locator(`[data-picker-mode="${mode}"]`), isMobile);
    await expect(menu).toBeHidden();
    await expect(button).toHaveAttribute('aria-expanded', 'false');
    await expect(page.locator('#viewportPanel')).toHaveAttribute('data-transform-tool', mode);
    await expect(button.locator('[data-current-tool]')).toHaveText(mode[0]!.toUpperCase() + mode.slice(1));
    await expect(page.locator('[data-layer-id="0"]')).toHaveAttribute('aria-selected', 'true');
    expect(await outline(page, 0)).toBe(selected);
  }
  await expect(page.locator('#transformDock [data-action="undo"]')).toBeDisabled();
  expect(await colors(page)).toEqual(pixels);
});

test('Cancel, Escape and outside taps dismiss without click-through or losing focus', async ({ page, isMobile }) => {
  await fixture(page); await select(page, 0);
  const menu = page.locator('#toolModePicker'), button = page.locator('#toolModeButton');
  for (const dismissal of ['cancel', 'escape', 'outside']) {
    await open(page, isMobile);
    if (dismissal === 'cancel') await activate(menu.locator('[data-picker-cancel]'), isMobile);
    else if (dismissal === 'escape') await page.keyboard.press('Escape');
    else {
      // The modal backdrop must consume the entire outside tap, not select a sprite.
      const b = (await menu.boundingBox())!, viewport = page.viewportSize()!;
      const point = { x: Math.min(viewport.width - 2, b.x + b.width / 2), y: b.y > 4 ? 2 : viewport.height - 2 };
      if (isMobile) await page.touchscreen.tap(point.x, point.y); else await page.mouse.click(point.x, point.y);
    }
    await expect(menu).toBeHidden(); await expect(button).toBeFocused();
    await expect(page.locator('#viewportPanel')).toHaveAttribute('data-transform-tool', 'select');
    await expect(page.locator('[data-layer-id="0"]')).toHaveAttribute('aria-selected', 'true');
    await expect(page.locator('[data-transform-field="offsetX"]')).toHaveValue('0');
    await expect(page.locator('#transformDock [data-action="undo"]')).toBeDisabled();
  }
});

test('keyboard navigation is contained and shortcuts cannot edit sprites behind the menu', async ({ page, isMobile }) => {
  await fixture(page); await select(page, 0);
  await page.locator('#toolModeButton').focus(); await page.keyboard.press('Space');
  const menu = page.locator('#toolModePicker');
  await expect(menu).toBeVisible();
  await expect(menu.locator('[data-picker-mode="select"]')).toBeFocused();
  await page.keyboard.press('ArrowRight'); await expect(menu.locator('[data-picker-mode="move"]')).toBeFocused();
  await page.keyboard.press('e'); await page.keyboard.press('Control+z');
  await expect(page.locator('#viewportPanel')).toHaveAttribute('data-transform-tool', 'select');
  await expect(page.locator('[data-transform-field="offsetX"]')).toHaveValue('0');
  await page.keyboard.press('End'); await expect(menu.locator('[data-picker-cancel]')).toBeFocused();
  await page.keyboard.press('Tab'); await expect(menu.locator('[data-picker-mode="select"]')).toBeFocused();
  await page.keyboard.press('ArrowDown'); await expect(menu.locator('[data-picker-mode="scale"]')).toBeFocused();
  await page.keyboard.press('Enter'); await expect(menu).toBeHidden();
  await expect(page.locator('#viewportPanel')).toHaveAttribute('data-transform-tool', 'scale');
  await open(page, isMobile); await expect(menu.locator('[data-picker-mode="scale"]')).toBeFocused();
  await page.keyboard.press('Escape'); await expect(menu).toBeHidden();
});

test('reduced motion, repeated opening, resize and blur leave no stale modal', async ({ page, isMobile }) => {
  await fixture(page); await select(page, 0);
  const menu = page.locator('#toolModePicker');
  await page.emulateMedia({ reducedMotion: 'reduce' });
  for (let i = 0; i < 3; i++) {
    await open(page, isMobile);
    expect(await menu.evaluate((node) => node.getAnimations().length)).toBe(0);
    await activate(menu.locator('[data-picker-mode="rotate"]'), isMobile);
    await expect(menu).toBeHidden();
  }
  await page.emulateMedia({ reducedMotion: 'no-preference' });
  await open(page, isMobile);
  const size = page.viewportSize()!; await page.setViewportSize({ ...size, width: size.width + 1 });
  await expect(menu).toBeHidden();
  await open(page, isMobile); await page.evaluate(() => window.dispatchEvent(new Event('blur')));
  await expect(menu).toBeHidden();
  await open(page, isMobile);
  await expect(menu.locator('[data-picker-mode="rotate"]')).toHaveAttribute('aria-pressed', 'true');
});

test('touch taskbar remains compact; mouse shortcuts and inspector fallback remain available', async ({ page, isMobile }) => {
  await fixture(page); await select(page, 0);
  const touchFirst = await page.evaluate(() => matchMedia('(pointer: coarse) and (hover: none)').matches);
  const tools = page.locator('#transformDock .transform-tools');
  if (touchFirst) {
    await expect(tools).toBeHidden();
    expect(await page.locator('#transformDock').evaluate((node) => node.getBoundingClientRect().height)).toBeLessThan(65);
    await inspector(page); await expect(page.locator('#touchToolSelect')).toBeVisible();
    await page.locator('#touchToolSelect').selectOption('pan');
    await expect(page.locator('#toolModeButton [data-current-tool]')).toHaveText('Pan');
  } else await expect(tools).toBeVisible();
  await open(page, isMobile); await activate(page.locator('[data-picker-mode="move"]'), isMobile);
  await expect(page.locator('#toolModePicker')).toBeHidden();
  await expect(page.locator('#transformDock [data-action="right"]')).toBeVisible();
  await activate(page.locator('#transformDock [data-action="right"]'), isMobile);
  await expect(page.locator('[data-transform-field="offsetX"]')).toHaveValue('1');
  // A native touch compatibility click is consumed, but a new mouse press or
  // keyboard activation immediately afterwards must still work exactly once.
  await page.locator('#transformDock [data-action="right"]').click();
  await expect(page.locator('[data-transform-field="offsetX"]')).toHaveValue('2');
  await page.locator('#transformDock [data-action="right"]').focus();
  await page.keyboard.press('Space');
  await expect(page.locator('[data-transform-field="offsetX"]')).toHaveValue('3');
  for (let value = 2; value >= 0; value--) {
    await activate(page.locator('#transformDock [data-action="undo"]'), isMobile);
    await expect(page.locator('[data-transform-field="offsetX"]')).toHaveValue(String(value));
  }
  await expect(page.locator('#transformDock [data-action="undo"]')).toBeDisabled();
});

test('tiles fit narrow phones, landscape and UI scaling without horizontal overflow', async ({ page, isMobile }, info) => {
  await fixture(page);
  for (const size of [{ width: 320, height: 568 }, { width: 393, height: 727 }, { width: 844, height: 390 }]) {
    await page.setViewportSize(size);
    for (const scale of [1, 1.25]) {
      await page.evaluate((value) => {
        document.documentElement.style.setProperty('--ui-scale', String(value));
        document.documentElement.style.setProperty('--ui-scale-inverse', String(1 / value));
      }, scale);
      await open(page, isMobile);
      const menu = page.locator('#toolModePicker'), bounds = (await menu.boundingBox())!;
      expect(bounds.x).toBeGreaterThanOrEqual(0); expect(bounds.y).toBeGreaterThanOrEqual(0);
      expect(bounds.x + bounds.width).toBeLessThanOrEqual(size.width + 1);
      expect(bounds.y + bounds.height).toBeLessThanOrEqual(size.height + 1);
      expect(await menu.evaluate((node) => node.scrollWidth <= node.clientWidth)).toBe(true);
      for (const tile of await menu.locator('button').all()) {
        const b = (await tile.boundingBox())!;
        expect(b.width).toBeGreaterThanOrEqual(44); expect(b.height).toBeGreaterThanOrEqual(44);
        expect(b.x).toBeGreaterThanOrEqual(bounds.x); expect(b.x + b.width).toBeLessThanOrEqual(bounds.x + bounds.width);
      }
      await page.screenshot({ path: info.outputPath(`popout-${size.width}-${scale}.png`) });
      await activate(menu.locator('[data-picker-cancel]'), isMobile); await expect(menu).toBeHidden();
    }
  }
});

test('triple tapping sprites now only selects; tools no longer require a viewport gesture', async ({ page, isMobile }) => {
  test.skip(!isMobile, 'Native taps need a touch-enabled profile.');
  await fixture(page); await select(page, 0);
  const p = await center(page, 0);
  for (let i = 0; i < 3; i++) await page.touchscreen.tap(p.x, p.y);
  await expect(page.locator('#toolModePicker')).toBeHidden();
  await expect(page.locator('[data-layer-id="0"]')).toHaveAttribute('aria-selected', 'true');
  await expect(page.locator('#transformDock [data-action="undo"]')).toBeDisabled();
});
