import { expect, type Page } from '@playwright/test';
import { readFileSync } from 'node:fs';

export async function fixture(page: Page) {
  await page.goto('.');
  const image = await page.evaluate(() => {
    const canvas = document.createElement('canvas'); canvas.width = 48; canvas.height = 16;
    const ctx = canvas.getContext('2d')!;
    ['#ff0000', '#00ff00', '#0000ff'].forEach((color, i) => { ctx.fillStyle = color; ctx.fillRect(i * 16, 0, 16, 16); });
    return canvas.toDataURL().split(',')[1]!;
  });
  const track = { name: 'Transform test', width: 160, height: 100, layers: [0, 1, 2].map((id) => ({
    id, name: ['parent', 'child', 'grandchild'][id], parent: id - 1, src: 0, anchor_x: 8, anchor_y: 8,
    frames: [{ time: 0, pos: { immediate: 0, x: 40, y: id ? 8 : 50 }, sprite: { immediate: 1, string: `sprite${id}` } },
      { time: 1, pos: { immediate: 0, x: id ? 40 : 60, y: id ? 8 : 50 } }],
  })) };
  await page.locator('#fileInput').setInputFiles([
    { name: 'transforms.json', mimeType: 'application/json', buffer: Buffer.from(JSON.stringify({ rev: 6, sources: [{ id: 0, src: 'atlas.xml' }], anims: [track, { ...track, name: 'Second track' }] })) },
    { name: 'atlas.xml', mimeType: 'application/xml', buffer: Buffer.from(`<TextureAtlas imagePath="atlas.png" width="48" height="16">${[0, 1, 2].map((id) => `<sprite n="sprite${id}" x="${id * 16}" y="0" w="16" h="16" oW="16" oH="16"/>`).join('')}</TextureAtlas>`) },
    { name: 'atlas.png', mimeType: 'image/png', buffer: Buffer.from(image, 'base64') },
  ]);
  await expect(page.locator('#layerCount')).toHaveText('3');
  await expect(page.locator('#transformDock')).toBeVisible();
}

export async function panel(page: Page, panel: 'layers' | 'library') {
  const tab = page.locator(`.mobile-panel-tabs [data-panel="${panel}"]`);
  if (await tab.isVisible()) await tab.click();
}
export async function select(page: Page, id: number) {
  await panel(page, 'layers');
  await page.locator(`[data-layer-id="${id}"] .row-name`).click();
  await expect(page.locator(`[data-selection-outline="${id}"]`)).toHaveCount(1);
}
export async function inspector(page: Page) {
  await panel(page, 'library');
  await page.locator('#controlTab-sprites').click();
}
export async function chooseTool(page: Page, mode: string) {
  const button = page.locator(`[data-transform-tool="${mode}"]`);
  if (await button.isVisible()) await button.click();
  else {
    await page.locator('#toolModeButton').click();
    await page.locator(`#toolModePicker [data-picker-mode="${mode}"]`).click();
    await expect(page.locator('#toolModePicker')).toBeHidden();
    await page.locator('#viewportPanel').evaluate((host) => host.scrollIntoView({ block: 'start' }));
  }
}
export async function field(page: Page, key: string, value: string) {
  await inspector(page);
  const input = page.locator(`[data-transform-field="${key}"]`);
  await input.fill(value); await input.dispatchEvent('change');
}
export async function outline(page: Page, id: number) {
  return page.locator(`[data-selection-outline="${id}"]`).getAttribute('points');
}
export async function center(page: Page, id: number) {
  return page.locator(`[data-selection-outline="${id}"]`).evaluate((node) => {
    const rect = node.getBoundingClientRect(); return { x: rect.x + rect.width / 2, y: rect.y + rect.height / 2, width: rect.width, height: rect.height };
  });
}
export async function colors(page: Page) {
  await panel(page, 'library'); await page.locator('#controlTab-export').click();
  await page.locator('#exportFramingSelect').selectOption('animation');
  const download = page.waitForEvent('download'); await page.locator('#exportFrameButton').click();
  const png = readFileSync((await (await download).path())!).toString('base64');
  return page.evaluate(async (base64) => {
    const image = await createImageBitmap(await (await fetch(`data:image/png;base64,${base64}`)).blob());
    const canvas = document.createElement('canvas'); canvas.width = image.width; canvas.height = image.height;
    const ctx = canvas.getContext('2d')!; ctx.drawImage(image, 0, 0); image.close();
    const pixels = ctx.getImageData(0, 0, canvas.width, canvas.height).data;
    const result: number[][] = [[], [], []];
    for (let i = 0; i < pixels.length; i += 4) for (let c = 0; c < 3; c++) {
      if (pixels[i + c]! > 200 && pixels[i + (c + 1) % 3]! < 20 && pixels[i + (c + 2) % 3]! < 20 && pixels[i + 3]! > 100) result[c]!.push(i / 4);
    }
    return result;
  }, png);
}
