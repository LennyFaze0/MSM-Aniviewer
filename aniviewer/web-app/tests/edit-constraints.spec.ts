import { test, expect, type Page } from '@playwright/test';
import { fixture, select, inspector, field, colors, center, chooseTool } from './helpers/sprite-editing';
import { readFileSync } from 'node:fs';
import { basename, dirname, resolve } from 'node:path';
import { parseAnimationBin } from '../src/core/bin-parser';

async function links(page: Page, mode: 'rigid' | 'selective' | 'off', driver = 0) {
  await inspector(page);
  await page.locator('#editLinkDriver').selectOption(String(driver));
  await page.locator('#editLinkMode').selectOption(mode);
  if (mode !== 'off') await page.locator('[data-link="descendants"]').click();
}
async function apply(page: Page) {
  await page.locator('[data-link="apply"]').click();
  await expect(page.locator('#editLinkError')).toBeEmpty();
}

test('rigid link uses one driver; exports, undo, off and relink preserve the visible pose', async ({ page }) => {
  await fixture(page); await select(page, 0);
  const original = await colors(page);
  await links(page, 'rigid'); await apply(page);
  expect(await colors(page)).toEqual(original);
  await expect(page.locator('[data-linked-outline="1"]')).toHaveCount(1);
  await expect(page.locator('[data-linked-outline="2"]')).toHaveCount(1);
  await field(page, 'offsetX', '-12');
  const moved = await colors(page);
  for (const id of [0, 1, 2]) expect(moved[id]).not.toEqual(original[id]);
  await page.locator('#transformDock [data-action="undo"]').click();
  expect(await colors(page)).toEqual(original);
  await page.locator('#transformDock [data-action="redo"]').click();
  expect(await colors(page)).toEqual(moved);
  await links(page, 'off'); await apply(page);
  expect(await colors(page)).toEqual(moved);
  await field(page, 'rotation', '38'); await field(page, 'scaleX', '140');
  const off = await colors(page);
  expect(off[0]).not.toEqual(moved[0]);
  expect(off[1]).toEqual(moved[1]); expect(off[2]).toEqual(moved[2]);
  await links(page, 'rigid'); await apply(page);
  expect(await colors(page)).toEqual(off);
  // Selecting a rigid follower edits the driver, not an independently pivoted child.
  const layerTab = page.locator('.mobile-panel-tabs [data-panel="layers"]');
  if (await layerTab.isVisible()) await layerTab.click();
  await page.locator('[data-layer-id="2"] .row-name').click();
  await inspector(page);
  await expect(page.locator('#transformSelection')).toContainText('Editing linked driver: parent');
  await expect(page.locator('[data-transform-field="rotation"]')).toHaveValue('38');
});

test('selective links leave followers unchanged on blocked transforms; hidden followers still participate', async ({ page }) => {
  await fixture(page); await select(page, 0);
  const original = await colors(page);
  await links(page, 'selective');
  await page.locator('[data-link-channel="rotation"]').uncheck();
  await page.locator('[data-link-channel="scale"]').uncheck();
  await apply(page);
  await field(page, 'rotation', '30'); await field(page, 'scaleX', '140');
  const rotated = await colors(page);
  expect(rotated[1]).toEqual(original[1]); expect(rotated[2]).toEqual(original[2]);
  const layerTab = page.locator('.mobile-panel-tabs [data-panel="layers"]');
  if (await layerTab.isVisible()) await layerTab.click();
  await page.locator('[data-layer-id="1"] .visibility-button').click();
  await field(page, 'offsetY', '-9');
  if (await layerTab.isVisible()) await layerTab.click();
  await page.locator('[data-layer-id="1"] .visibility-button').click();
  const moved = await colors(page);
  expect(moved[1]).not.toEqual(original[1]); expect(moved[2]).not.toEqual(original[2]);
  await page.locator('#transformDock [data-action="undo"]').click();
  expect(await colors(page)).toEqual(rotated);
});

test('invalid links show actionable errors without changing the pose; reset is undoable', async ({ page }) => {
  await fixture(page); await select(page, 0);
  await links(page, 'rigid'); await apply(page);
  await field(page, 'rotation', '25'); await field(page, 'scaleX', '130');
  const edited = await colors(page);
  await links(page, 'rigid', 2);
  await page.locator('[data-link-member="0"]').check();
  await page.locator('[data-link="apply"]').click();
  await expect(page.locator('#editLinkError')).toContainText('cycle');
  expect(await colors(page)).toEqual(edited);
  await inspector(page); await page.locator('[data-action="reset"]').click();
  const reset = await colors(page); expect(reset).not.toEqual(edited);
  await page.locator('#transformDock [data-action="undo"]').click();
  expect(await colors(page)).toEqual(edited);
});

test('linked pointer drag moves all outlines by the same amount and undoes atomically', async ({ page, isMobile, browserName }) => {
  await fixture(page); await select(page, 0); await links(page, 'rigid'); await apply(page);
  await chooseTool(page, 'move');
  const positions = () => page.locator('[data-selection-outline], [data-linked-outline]').evaluateAll((nodes) => nodes.map((node) => {
    const b = node.getBoundingClientRect(); return { id: node.getAttribute('data-selection-outline') ?? node.getAttribute('data-linked-outline'), x: b.x, y: b.y };
  }));
  const before = await positions(), p = await center(page, 0);
  if (isMobile && browserName === 'chromium') {
    const cdp = await page.context().newCDPSession(page);
    await cdp.send('Input.dispatchTouchEvent', { type: 'touchStart', touchPoints: [{ id: 1, x: p.x, y: p.y }] });
    for (let i = 1; i <= 6; i++) await cdp.send('Input.dispatchTouchEvent', { type: 'touchMove', touchPoints: [{ id: 1, x: p.x - i * 3, y: p.y }] });
    await cdp.send('Input.dispatchTouchEvent', { type: 'touchEnd', touchPoints: [] });
    await cdp.detach();
  } else {
    await page.mouse.move(p.x, p.y); await page.mouse.down(); await page.mouse.move(p.x - 18, p.y, { steps: 6 }); await page.mouse.up();
  }
  await expect.poll(async () => Number(await page.locator('[data-transform-field="offsetX"]').inputValue())).toBeLessThan(-1);
  const after = await positions();
  for (const original of before) {
    const next = after.find((entry) => entry.id === original.id)!;
    expect(next.x - original.x).toBeCloseTo(-18, 0);
    expect(next.y).toBeCloseTo(original.y, 1);
  }
  await page.locator('#transformDock [data-action="undo"]').click();
  expect(await positions()).toEqual(before);
});

test('local game asset: visible follower corners use the same affine map across existing animation keys', async ({ page }) => {
  const asset = process.env.ANIVIEWER_CONSTRAINT_ASSET;
  test.skip(!asset, 'Optional private BIN/XML/spritesheet fixture; no game assets are shipped.');
  const bytes = readFileSync(asset!);
  const document = parseAnimationBin(bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset + bytes.byteLength), 'auto', asset!).document;
  const root = resolve(dirname(asset!), '..');
  const files = [asset!];
  for (const source of document.sources) {
    const xml = resolve(root, 'xml_resources', basename(source.src));
    files.push(xml);
    const image = /imagePath="([^"]+)"/.exec(readFileSync(xml, 'utf8'))?.[1];
    if (image) files.push(resolve(root, image.replace(/\.png$/i, '.avif')));
  }
  await page.goto('.'); await page.locator('#fileInput').setInputFiles(files);
  const track = document.anims[0]!;
  await expect(page.locator('#layerCount')).toHaveText(String(track.layers.length));
  const driver = [...track.layers].sort((a, b) => track.layers.filter((l) => l.parent === b.id).length - track.layers.filter((l) => l.parent === a.id).length)[0]!;
  await select(page, driver.id); await links(page, 'rigid', driver.id); await apply(page);
  const corners = () => page.locator('[data-selection-outline], [data-linked-outline]').evaluateAll((nodes) => Object.fromEntries(nodes.map((node) => [node.getAttribute('data-selection-outline') ?? node.getAttribute('data-linked-outline'), node.getAttribute('points')!.split(' ').map((point) => point.split(',').map(Number))])));
  const times = [...new Set([0, ...driver.frames.map((frame) => frame.time)])];
  const samples = [times[0]!, times[Math.floor(times.length / 3)]!, times[Math.floor(times.length * 2 / 3)]!];
  const scrub = async (time: number) => {
    await page.locator('#timeline').evaluate((node, value) => { (node as HTMLInputElement).value = String(value); node.dispatchEvent(new Event('input', { bubbles: true })); }, time);
  };
  const originals = [];
  for (const time of samples) { await scrub(time); originals.push(await corners()); }
  await field(page, 'rotation', '31'); await field(page, 'scaleX', '135'); await field(page, 'scaleY', '80');
  for (const [index, time] of samples.entries()) {
    await scrub(time);
    const before = originals[index]!, after = await corners();
    const p = before[driver.id]!, q = after[driver.id]!;
    const ax = p[1]![0]! - p[0]![0]!, ay = p[1]![1]! - p[0]![1]!;
    const bx = p[3]![0]! - p[0]![0]!, by = p[3]![1]! - p[0]![1]!;
    const det = ax * by - ay * bx;
    expect(Math.abs(det)).toBeGreaterThan(1);
    expect(Object.keys(after).length).toBeGreaterThan(2);
    for (const [id, points] of Object.entries(before)) for (const [corner, point] of points.entries()) {
      const x = point[0]! - p[0]![0]!, y = point[1]! - p[0]![1]!;
      const u = (x * by - y * bx) / det, v = (ax * y - ay * x) / det;
      for (const axis of [0, 1]) {
        const expected = q[0]![axis]! + u * (q[1]![axis]! - q[0]![axis]!) + v * (q[3]![axis]! - q[0]![axis]!);
        expect(after[id]![corner]![axis]!).toBeCloseTo(expected, 6);
      }
    }
  }
});
