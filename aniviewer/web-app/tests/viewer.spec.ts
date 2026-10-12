import { expect, test } from '@playwright/test';
import { existsSync, readFileSync } from 'node:fs';
import { deflateSync } from 'node:zlib';
import {
  nativeRev6Fixture,
  zippedDuplicateAtlasFixture,
  zippedNativeFixture,
} from './native-fixture';

function silentWav(duration: number, sampleRate = 8_000): Buffer {
  const sampleCount = Math.round(duration * sampleRate);
  const dataSize = sampleCount * 2;
  const wav = Buffer.alloc(44 + dataSize);
  wav.write('RIFF', 0);
  wav.writeUInt32LE(36 + dataSize, 4);
  wav.write('WAVE', 8);
  wav.write('fmt ', 12);
  wav.writeUInt32LE(16, 16);
  wav.writeUInt16LE(1, 20);
  wav.writeUInt16LE(1, 22);
  wav.writeUInt32LE(sampleRate, 24);
  wav.writeUInt32LE(sampleRate * 2, 28);
  wav.writeUInt16LE(2, 32);
  wav.writeUInt16LE(16, 34);
  wav.write('data', 36);
  wav.writeUInt32LE(dataSize, 40);
  return wav;
}

function crc32(data: Buffer): number {
  let crc = 0xffffffff;
  for (const byte of data) {
    crc ^= byte;
    for (let bit = 0; bit < 8; bit += 1) {
      crc = (crc >>> 1) ^ (0xedb88320 & -(crc & 1));
    }
  }
  return (crc ^ 0xffffffff) >>> 0;
}

function pngChunk(type: string, data: Buffer): Buffer {
  const name = Buffer.from(type, 'ascii');
  const chunk = Buffer.alloc(12 + data.length);
  chunk.writeUInt32BE(data.length, 0);
  name.copy(chunk, 4);
  data.copy(chunk, 8);
  chunk.writeUInt32BE(crc32(Buffer.concat([name, data])), 8 + data.length);
  return chunk;
}

function solidRgbaPng(red: number, green: number, blue: number, alpha: number): Buffer {
  const width = 4;
  const height = 4;
  const header = Buffer.alloc(13);
  header.writeUInt32BE(width, 0);
  header.writeUInt32BE(height, 4);
  header[8] = 8;
  header[9] = 6;
  const rows: number[] = [];
  for (let y = 0; y < height; y += 1) {
    rows.push(0);
    for (let x = 0; x < width; x += 1) rows.push(red, green, blue, alpha);
  }
  return Buffer.concat([
    Buffer.from('89504e470d0a1a0a', 'hex'),
    pngChunk('IHDR', header),
    pngChunk('IDAT', deflateSync(Buffer.from(rows))),
    pngChunk('IEND', Buffer.alloc(0)),
  ]);
}

function alphaFixtureDocument(): object {
  return {
    rev: 6,
    sources: [{ src: 'alpha.png', id: 0 }],
    anims: [{
      name: 'Idle',
      width: 64,
      height: 64,
      layers: [{
        name: 'alpha',
        type: 1,
        parent: -1,
        id: 0,
        src: 0,
        anchor_x: 0.5,
        anchor_y: 0.5,
        frames: [{
          time: 0,
          pos: { immediate: 0, x: 32, y: 32 },
          scale: { immediate: 0, x: 800, y: 800 },
          opacity: { immediate: 0, value: 100 },
          sprite: { immediate: 0, string: 'alpha' },
        }],
      }],
    }],
  };
}

function muppetsAudioManifest(trackName: string, audioPath: string): Buffer {
  const bytes: number[] = [];
  const u32 = (value: number): void => {
    bytes.push(value & 0xff, (value >>> 8) & 0xff, (value >>> 16) & 0xff, (value >>> 24) & 0xff);
  };
  const string = (value: string): void => {
    const encoded = Buffer.from(`${value}\0`, 'utf8');
    u32(encoded.length);
    bytes.push(...encoded);
    while (bytes.length % 4) bytes.push(0);
  };
  string('rawlf');
  string('rawlf animation');
  u32(110);
  u32(1);
  u32(72);
  string(audioPath);
  u32(2);
  u32(72);
  u32(110);
  string(trackName);
  u32(73);
  u32(110);
  string('Idle');
  return Buffer.from(bytes);
}

test('opens a usable local viewer without horizontal overflow', async ({ page }) => {
  await page.goto('.');

  await expect(page.getByText('MSM Aniviewer')).toBeVisible();
  await expect(page.locator('.viewport-canvas')).toBeVisible();
  await expect(page.getByText('Local processing')).toBeVisible();
  await expect(page.locator('.app-header #dofGrabberButton')).toHaveCount(0);
  await expect(page.locator('.app-header #importFilesButton')).toHaveCount(0);
  await expect(page.locator('.app-header #importFolderButton')).toHaveCount(0);
  await expect(page.locator('.library-panel #importFolderButton')).toHaveText('Open Game Folder');
  await expect(page.locator('.library-panel #importFilesButton')).toHaveText('Open Animation Files');
  await expect(page.locator('.library-panel #dofGrabberButton')).toHaveText('Dawn of Fire Asset Grabber');
  await expect(page.locator('#revisionModeSelect option')).toHaveText([
    'Automatic',
    'Rev 1',
    'Rev 1 (Legacy Mobile)',
    'Rev 2',
    'Rev 2 (My Muppets Show)',
    'Rev 3 (Monster Choir)',
    'Rev 4',
    'Rev 5',
    'Rev 6',
  ]);

  const canvasSize = await page.locator('.viewport-canvas').evaluate((canvas) => ({
    width: (canvas as HTMLCanvasElement).width,
    height: (canvas as HTMLCanvasElement).height,
  }));
  expect(canvasSize.width).toBeGreaterThan(100);
  expect(canvasSize.height).toBeGreaterThan(100);

  const pageWidth = await page.evaluate(() => ({
    client: document.documentElement.clientWidth,
    scroll: document.documentElement.scrollWidth,
  }));
  expect(pageWidth.scroll).toBeLessThanOrEqual(pageWidth.client);

  await page.locator('#controlTab-animation').click();
  const libraryLayout = await page.evaluate(() => {
    const panel = document.querySelector('.library-panel')?.getBoundingClientRect();
    const tracks = document.querySelector('#trackList')?.getBoundingClientRect();
    return panel && tracks
      ? { panelTop: panel.top, panelBottom: panel.bottom, trackTop: tracks.top, trackBottom: tracks.bottom, trackHeight: tracks.height }
      : undefined;
  });
  expect(libraryLayout).toBeDefined();
  expect(libraryLayout?.trackTop).toBeGreaterThanOrEqual(libraryLayout?.panelTop ?? 0);
  expect(libraryLayout?.trackBottom).toBeLessThanOrEqual((libraryLayout?.panelBottom ?? 0) + 1);
  expect(libraryLayout?.trackHeight).toBeGreaterThanOrEqual(80);
});

test('provides the desktop control-panel sections with accessible tab navigation', async ({ page }) => {
  await page.goto('.');

  const tabs = page.locator('.control-tab-list [role="tab"]');
  await expect(tabs).toHaveText([
    'Files',
    'Animation',
    'Playback',
    'Viewport',
    '3D',
    'Audio',
    'Sprites',
    'Terrain',
    'Export',
    'Rigging',
    'System',
  ]);
  await expect(page.locator('#controlTab-files')).toHaveAttribute('aria-selected', 'true');
  await expect(page.locator('#controlPanel-files')).toBeVisible();
  await expect(page.locator('#controlPanel-animation')).toBeHidden();

  await page.locator('#controlTab-files').press('ArrowRight');
  await expect(page.locator('#controlTab-animation')).toHaveAttribute('aria-selected', 'true');
  await expect(page.locator('#controlPanel-files')).toBeHidden();
  await expect(page.locator('#controlPanel-animation')).toBeVisible();

  await page.locator('#controlTab-animation').press('End');
  await expect(page.locator('#controlTab-system')).toHaveAttribute('aria-selected', 'true');
  await expect(page.locator('#controlPanel-system')).toBeVisible();

  await page.locator('#controlTab-export').click();
  await expect(page.locator('#exportFrameButton')).toBeDisabled();
  await expect(page.locator('#exportSequenceButton')).toBeDisabled();
  await expect(page.locator('#exportCancelButton')).toBeHidden();

  const pageWidth = await page.evaluate(() => ({
    client: document.documentElement.clientWidth,
    scroll: document.documentElement.scrollWidth,
  }));
  expect(pageWidth.scroll).toBeLessThanOrEqual(pageWidth.client);
});

test('opens a top settings drawer with persisted browser-native preferences', async ({ page }) => {
  await page.goto('.');

  await page.locator('#settingsButton').click();
  await expect(page.locator('#settingsLayer')).toHaveAttribute('data-open', 'true');
  await expect(page.locator('.settings-tabs [role="tab"]')).toHaveText([
    'Export', 'Tools', 'Paths', 'Keybinds', 'Application',
  ]);
  await expect(page.locator('#settingsLayer')).not.toContainText('BIN Converter');

  await page.locator('[data-setting="interface.themeMode"]').selectOption('light');
  await page.locator('[data-setting="interface.scale"]').fill('1.1');
  await expect(page.locator('html')).toHaveAttribute('data-theme-mode', 'light');
  const scaledShell = await page.locator('.app-shell').boundingBox();
  expect(scaledShell?.width).toBeCloseTo(page.viewportSize()!.width, 0);
  expect(scaledShell?.height).toBeCloseTo(page.viewportSize()!.height, 0);
  await page.locator('[data-settings-action="cancel"]').last().click();
  await expect(page.locator('#settingsLayer')).toBeHidden();
  await expect(page.locator('html')).toHaveAttribute('data-theme-mode', 'system');

  await page.locator('#settingsButton').click();
  await page.locator('[data-setting="interface.themeMode"]').selectOption('dark');
  await page.locator('[data-settings-section="viewport"]').click();
  await page.locator('[data-setting="viewport.zoomToCursor"]').check();
  await page.locator('[data-settings-action="save"]').click();
  await expect(page.locator('#settingsLayer')).toBeHidden();
  await page.reload();
  await expect(page.locator('html')).toHaveAttribute('data-theme-mode', 'dark');
  await expect(page.locator('#settingsButton')).toHaveAttribute('aria-expanded', 'false');

  await page.locator('#settingsButton').click();
  await page.locator('[data-settings-section="conversion"]').click();
  await page.locator('[data-setting="conversion.automaticRevisionDetection"]').uncheck();
  await page.locator('[data-setting="conversion.manualRevisionMode"]').selectOption('3');
  await page.locator('[data-settings-tab="keybinds"]').click();
  await page.locator('[data-keybind="toggle_controls_panel"]').press('b');
  await page.locator('[data-settings-action="save"]').click();
  await expect(page.locator('#revisionModeSelect')).toHaveValue('3');
  await page.keyboard.press('b');
  await expect(page.locator('.app-shell')).toHaveClass(/controls-panel-hidden/);
  await page.reload();
  await expect(page.locator('#revisionModeSelect')).toHaveValue('3');
});

test('loads a native Rev 6 BIN through the parser worker', async ({ page }) => {
  const fixture = nativeRev6Fixture();
  await page.goto('.');
  await page.locator('#fileInput').setInputFiles([
    { name: 'monster_test.bin', mimeType: 'application/octet-stream', buffer: Buffer.from(fixture.bin) },
    { name: 'atlas.xml', mimeType: 'application/xml', buffer: Buffer.from(fixture.xml) },
    { name: 'atlas.png', mimeType: 'image/png', buffer: fixture.png },
  ]);

  await expect(page.locator('#statusText')).toHaveText('1 animation loaded from BIN');
  await expect(page.locator('#revisionValue')).toHaveText('Rev 6');
  await expect(page.locator('#trackList [data-track-index]')).toHaveCount(1);
  await expect(page.locator('#layerList [data-layer-id]')).toHaveCount(1);
  await expect(page.locator('#viewportEmpty')).toBeHidden();
});

test('exports the current composed frame as a transparent native-canvas PNG', async ({ page }) => {
  const fixture = nativeRev6Fixture();
  const document = {
    rev: 6,
    sources: [{ src: 'atlas.xml', id: 0 }],
    anims: [{
      name: 'Export Idle',
      width: 64,
      height: 64,
      layers: [{
        name: 'sprite', parent: -1, id: 0, src: 0, anchor_x: 0.5, anchor_y: 0.5,
        frames: [{
          time: 0,
          pos: { immediate: 0, x: 32, y: 32 },
          scale: { immediate: 0, x: 3200, y: 3200 },
          opacity: { immediate: 0, value: 100 },
          sprite: { immediate: 0, string: 'dot' },
        }, { time: 1, pos: { immediate: 0, x: 40, y: 32 } }],
      }],
    }],
  };

  await page.goto('.');
  await page.locator('#fileInput').setInputFiles([
    { name: 'export_test.json', mimeType: 'application/json', buffer: Buffer.from(JSON.stringify(document)) },
    { name: 'atlas.xml', mimeType: 'application/xml', buffer: Buffer.from(fixture.xml) },
    { name: 'atlas.png', mimeType: 'image/png', buffer: fixture.png },
  ]);
  await page.locator('#timeline').evaluate((element) => {
    element.value = '0.5';
    element.dispatchEvent(new Event('input', { bubbles: true }));
  });
  await page.locator('#controlTab-export').click();
  await page.locator('#exportScaleSelect').selectOption('2');

  const downloadPromise = page.waitForEvent('download');
  await page.locator('#exportFrameButton').click();
  const download = await downloadPromise;
  const path = await download.path();
  expect(path).toBeTruthy();
  const png = readFileSync(path!);

  expect(download.suggestedFilename()).toBe('Export_Idle_00016.png');
  expect(png.subarray(0, 8).toString('hex')).toBe('89504e470d0a1a0a');
  expect(png.readUInt32BE(16)).toBe(128);
  expect(png.readUInt32BE(20)).toBe(128);
  const alpha = await page.evaluate(async (base64) => {
    const image = new Image();
    image.src = `data:image/png;base64,${base64}`;
    await image.decode();
    const canvas = document.createElement('canvas');
    canvas.width = image.width;
    canvas.height = image.height;
    const context = canvas.getContext('2d')!;
    context.drawImage(image, 0, 0);
    const pixels = context.getImageData(0, 0, canvas.width, canvas.height).data;
    let maximum = 0;
    for (let index = 3; index < pixels.length; index += 4) maximum = Math.max(maximum, pixels[index]!);
    return { corner: pixels[3], maximum };
  }, png.toString('base64'));
  expect(alpha.corner).toBe(0);
  expect(alpha.maximum).toBeGreaterThan(0);
  await expect(page.locator('#timeline')).toHaveValue('0.5');
  await expect(page.locator('#statusText')).toHaveText('Current frame exported as PNG');
});

test('exports deterministic playback-rate-aware PNG sequences as ZIP downloads', async ({ page }) => {
  const fixture = nativeRev6Fixture();
  const document = {
    rev: 6,
    sources: [{ src: 'atlas.xml', id: 0 }],
    anims: [{
      name: 'Sequence Test', width: 64, height: 64,
      layers: [{
        name: 'sprite', parent: -1, id: 0, src: 0, anchor_x: 0.5, anchor_y: 0.5,
        frames: [{
          time: 0,
          pos: { immediate: 0, x: 32, y: 32 },
          scale: { immediate: 0, x: 3200, y: 3200 },
          opacity: { immediate: 0, value: 100 },
          sprite: { immediate: 0, string: 'dot' },
        }, { time: 1 }],
      }],
    }],
  };

  await page.goto('.');
  await page.locator('#fileInput').setInputFiles([
    { name: 'sequence_test.json', mimeType: 'application/json', buffer: Buffer.from(JSON.stringify(document)) },
    { name: 'atlas.xml', mimeType: 'application/xml', buffer: Buffer.from(fixture.xml) },
    { name: 'atlas.png', mimeType: 'image/png', buffer: fixture.png },
  ]);
  await page.locator('#controlTab-export').click();
  await page.locator('#exportFramingSelect').selectOption('content');
  await page.locator('#exportFpsInput').fill('4');

  const downloadPromise = page.waitForEvent('download');
  await page.locator('#exportSequenceButton').click();
  const download = await downloadPromise;
  const path = await download.path();
  expect(path).toBeTruthy();
  const archive = readFileSync(path!);

  expect(download.suggestedFilename()).toBe('Sequence_Test_png_sequence.zip');
  expect(archive.subarray(0, 4).toString('hex')).toBe('504b0304');
  await expect(page.locator('#statusText')).toHaveText('4 PNG frames exported');
  await expect(page.locator('#exportProgressText')).toContainText('4 frames ready');
  await expect(page.locator('#exportSequenceButton')).toBeEnabled();
});

test('renders premultiplied game and straight custom atlases identically', async ({ page }) => {
  const renderCase = async (
    rgba: readonly [number, number, number, number],
    format: 'premultiplied' | 'straight',
  ): Promise<number[]> => {
    await page.goto('.');
    await page.locator('#fileInput').setInputFiles([
      {
        name: 'monster_alpha.json',
        mimeType: 'application/json',
        buffer: Buffer.from(JSON.stringify(alphaFixtureDocument())),
      },
      {
        name: 'alpha.png',
        mimeType: 'image/png',
        buffer: solidRgbaPng(...rgba),
      },
    ]);

    await expect(page.locator('#statusText')).toHaveText('1 animation loaded from JSON');
    const formatAttribute = format === 'premultiplied'
      ? 'data-premultiplied-source-count'
      : 'data-straight-source-count';
    await expect(page.locator('#viewportPanel')).toHaveAttribute(formatAttribute, '1');
    const screenshot = await page.locator('.viewport-canvas').screenshot();
    return page.evaluate(async (encoded) => {
      const response = await fetch(`data:image/png;base64,${encoded}`);
      const image = await createImageBitmap(await response.blob());
      const canvas = document.createElement('canvas');
      canvas.width = image.width;
      canvas.height = image.height;
      const context = canvas.getContext('2d', { willReadFrequently: true })!;
      context.drawImage(image, 0, 0);
      const pixels = context.getImageData(0, 0, canvas.width, canvas.height).data;
      let best = 0;
      let bestScore = Number.NEGATIVE_INFINITY;
      for (let offset = 0; offset < pixels.length; offset += 4) {
        const score = pixels[offset]! - pixels[offset + 2]!;
        if (score <= bestScore) continue;
        best = offset;
        bestScore = score;
      }
      return [...pixels.slice(best, best + 4)];
    }, screenshot.toString('base64'));
  };

  const premultiplied = await renderCase([128, 64, 16, 128], 'premultiplied');
  const straight = await renderCase([255, 128, 32, 128], 'straight');
  expect(premultiplied[0]).toBeGreaterThan(100);
  straight.forEach((channel, index) => {
    expect(Math.abs(channel - premultiplied[index]!)).toBeLessThanOrEqual(2);
  });
});

test('selects layers independently from their visibility control', async ({ page }) => {
  const fixture = nativeRev6Fixture();
  await page.goto('.');
  await page.locator('#fileInput').setInputFiles([
    { name: 'monster_test.bin', mimeType: 'application/octet-stream', buffer: Buffer.from(fixture.bin) },
    { name: 'atlas.xml', mimeType: 'application/xml', buffer: Buffer.from(fixture.xml) },
    { name: 'atlas.png', mimeType: 'image/png', buffer: fixture.png },
  ]);

  const layer = page.locator('#layerList [data-layer-id="0"]');
  await expect(layer).toHaveCount(1);
  const layersTab = page.locator('.mobile-panel-tabs [data-panel="layers"]');
  if (await layersTab.isVisible()) await layersTab.click();
  await expect(layer).toBeVisible();
  await layer.press('Enter');
  await expect(layer).toHaveClass(/selected/);
  await expect(layer).toHaveAttribute('aria-selected', 'true');

  await layer.locator('.visibility-button').click();
  await expect(layer).toHaveClass(/selected/);
  await expect(layer).toHaveClass(/hidden-layer/);
});

test('indexes and loads native assets lazily from a standard ZIP', async ({ page }) => {
  await page.goto('.');
  await page.locator('#fileInput').setInputFiles({
    name: 'monster-assets.zip',
    mimeType: 'application/zip',
    buffer: await zippedNativeFixture(),
  });

  await expect(page.locator('#statusText')).toHaveText('1 animation loaded from BIN');
  await expect(page.locator('#fileCount')).toHaveText('3 files');
  await expect(page.locator('#revisionValue')).toHaveText('Rev 6');
});

test('resolves duplicate atlas names from the animation import tree', async ({ page }) => {
  await page.goto('.');
  await page.locator('#fileInput').setInputFiles({
    name: 'multiple-installs.zip',
    mimeType: 'application/zip',
    buffer: await zippedDuplicateAtlasFixture(),
  });

  await expect(page.locator('#statusText')).toHaveText('1 animation loaded from BIN');
  await expect(page.locator('#viewportEmpty')).toBeHidden();
});

test('recovers when missing atlas dependencies are added later', async ({ page }) => {
  const fixture = nativeRev6Fixture();
  await page.goto('.');
  await page.locator('#fileInput').setInputFiles({
    name: 'monster_test.bin',
    mimeType: 'application/octet-stream',
    buffer: Buffer.from(fixture.bin),
  });

  await expect(page.locator('#statusText')).toHaveText(
    'Missing required texture asset: atlas.xml. Open the game folder or add the matching atlas XML and spritesheet.',
  );
  await page.locator('#fileInput').setInputFiles([
    { name: 'atlas.xml', mimeType: 'application/xml', buffer: Buffer.from(fixture.xml) },
    { name: 'atlas.png', mimeType: 'image/png', buffer: fixture.png },
  ]);

  await expect(page.locator('#statusText')).toHaveText('1 animation loaded from BIN');
  await expect(page.locator('#viewportEmpty')).toBeHidden();
  await expect(page.locator('#fileCount')).toHaveText('3 files');
});

test('does not require audio-layer sources as texture atlases', async ({ page }) => {
  const fixture = nativeRev6Fixture();
  const document = {
    rev: 4,
    sources: [
      { src: 'atlas.xml', id: 0, width: 1, height: 1 },
      { src: 'authoring/reference.wav', id: 1, width: 0, height: 0 },
    ],
    anims: [{
      name: 'Mixed sources',
      width: 64,
      height: 64,
      layers: [
        {
          name: 'dot', type: 1, parent: -1, id: 0, src: 0, anchor_x: 0.5, anchor_y: 0.5,
          frames: [{ time: 0, pos: { immediate: 0, x: 32, y: 32 }, scale: { immediate: 0, x: 3200, y: 3200 } }],
        },
        {
          name: 'reference audio', type: 2, parent: -1, id: 1, src: 1, anchor_x: 0, anchor_y: 0,
          frames: [{ time: 0 }],
        },
      ],
    }],
  };

  await page.goto('.');
  await page.locator('#fileInput').setInputFiles([
    { name: 'mixed.json', mimeType: 'application/json', buffer: Buffer.from(JSON.stringify(document)) },
    { name: 'atlas.xml', mimeType: 'application/xml', buffer: Buffer.from(fixture.xml) },
    { name: 'atlas.png', mimeType: 'image/png', buffer: fixture.png },
  ]);

  await expect(page.locator('#statusText')).toHaveText('1 animation loaded from JSON');
  await expect(page.locator('#viewportEmpty')).toBeHidden();
});

test('loads available atlases when one texture source is still missing', async ({ page }) => {
  const fixture = nativeRev6Fixture();
  const document = {
    rev: 6,
    sources: [
      { src: 'atlas.xml', id: 0, width: 1, height: 1 },
      { src: 'secondary.xml', id: 1, width: 1, height: 1 },
    ],
    anims: [{
      name: 'Multiple atlases',
      width: 64,
      height: 64,
      layers: [0, 1].map((sourceId) => ({
        name: sourceId ? 'secondary' : 'dot',
        type: 1,
        parent: -1,
        id: sourceId,
        src: sourceId,
        anchor_x: 0.5,
        anchor_y: 0.5,
        frames: [{ time: 0, pos: { immediate: 0, x: 32, y: 32 }, scale: { immediate: 0, x: 3200, y: 3200 } }],
      })),
    }],
  };

  await page.goto('.');
  await page.locator('#fileInput').setInputFiles([
    { name: 'multiple.json', mimeType: 'application/json', buffer: Buffer.from(JSON.stringify(document)) },
    { name: 'atlas.xml', mimeType: 'application/xml', buffer: Buffer.from(fixture.xml) },
    { name: 'atlas.png', mimeType: 'image/png', buffer: fixture.png },
  ]);

  await expect(page.locator('#statusText')).toHaveText(
    '1 animation loaded from JSON; missing texture source: secondary.xml',
  );
  await expect(page.locator('#viewportEmpty')).toBeHidden();
  await expect(page.locator('#layerList [data-layer-id]')).toHaveCount(2);
});

test('explains a wrong manual revision and recovers with automatic detection', async ({ page }) => {
  const fixture = nativeRev6Fixture();
  await page.goto('.');
  await page.locator('#revisionModeSelect').selectOption('1');
  await page.locator('#fileInput').setInputFiles([
    { name: 'monster_test.bin', mimeType: 'application/octet-stream', buffer: Buffer.from(fixture.bin) },
    { name: 'atlas.xml', mimeType: 'application/xml', buffer: Buffer.from(fixture.xml) },
    { name: 'atlas.png', mimeType: 'image/png', buffer: fixture.png },
  ]);

  await expect(page.locator('#statusText')).toHaveText(
    'This animation BIN does not match Rev 1. Choose Automatic or select the correct revision.',
  );
  await page.locator('#revisionModeSelect').selectOption('auto');
  await expect(page.locator('#statusText')).toHaveText('1 animation loaded from BIN');
  await expect(page.locator('#revisionValue')).toHaveText('Rev 6');
});

test('loads a Muppets direct-image source without an XML atlas', async ({ page }) => {
  const image = nativeRev6Fixture().png;
  const document = {
    rev: 6,
    blend_version: 2,
    source_format: 'muppets',
    source_revision: 2,
    sources: [{ src: 'dot.png', id: 0, width: 0, height: 0 }],
    anims: [{
      name: 'Idle',
      width: 64,
      height: 64,
      layers: [{
        name: 'dot',
        parent: -1,
        id: 0,
        src: 0,
        anchor_x: 0.5,
        anchor_y: 0.5,
        frames: [{
          time: 0,
          pos: { immediate: 0, x: 32, y: 32 },
          scale: { immediate: 0, x: 3200, y: 3200 },
          opacity: { immediate: 0, value: 100 },
          sprite: { immediate: -1, string: '' },
        }],
      }],
    }],
  };

  await page.goto('.');
  await page.locator('#fileInput').setInputFiles([
    { name: 'muppet-placeholder.json', mimeType: 'application/json', buffer: Buffer.from(JSON.stringify(document)) },
    { name: 'dot.png', mimeType: 'image/png', buffer: image },
  ]);

  await expect(page.locator('#statusText')).toHaveText('1 animation loaded from JSON');
  await expect(page.locator('#revisionValue')).toHaveText('Rev 2 (My Muppets Show)');
  await expect(page.locator('#viewportEmpty')).toBeHidden();
});

test('resolves Muppets audio manifests and keeps utility animations silent', async ({ page }) => {
  const image = nativeRev6Fixture().png;
  const layer = {
    name: 'dot',
    type: 1,
    parent: -1,
    id: 0,
    src: 0,
    anchor_x: 0.5,
    anchor_y: 0.5,
    frames: [{
      time: 0,
      pos: { immediate: 0, x: 32, y: 32 },
      scale: { immediate: 0, x: 3200, y: 3200 },
      opacity: { immediate: 0, value: 100 },
      sprite: { immediate: -1, string: '' },
    }],
  };
  const document = {
    rev: 6,
    source_format: 'muppets',
    source_revision: 2,
    sources: [{ src: 'dot.png', id: 0 }],
    anims: ['01-rowlf_01', 'Idle', 'structure_muppetman_level2'].map((name) => ({
      name,
      width: 64,
      height: 64,
      layers: [layer],
    })),
  };

  await page.goto('.');
  await page.locator('#fileInput').setInputFiles([
    { name: 'muppet_rowlf_stage01.json', mimeType: 'application/json', buffer: Buffer.from(JSON.stringify(document)) },
    { name: 'dot.png', mimeType: 'image/png', buffer: image },
    {
      name: '001_rawlf.bin',
      mimeType: 'application/octet-stream',
      buffer: muppetsAudioManifest('01-rowlf_01', 'audio/music/manifest-rowlf.ogg'),
    },
    { name: 'manifest-rowlf.ogg', mimeType: 'audio/ogg', buffer: Buffer.from([10, 11, 12]) },
    { name: '01-rawlf_01.ogg', mimeType: 'audio/ogg', buffer: Buffer.from([1, 2, 3]) },
    { name: 'muppetman.ogg', mimeType: 'audio/ogg', buffer: Buffer.from([4, 5, 6]) },
    { name: 'idle.ogg', mimeType: 'audio/ogg', buffer: Buffer.from([7, 8, 9]) },
  ]);

  await expect(page.locator('#audioStatus')).toHaveText('manifest-rowlf.ogg');
  await page.locator('#trackList [data-track-index="1"]').click();
  await expect(page.locator('#audioStatus')).toHaveText('No audio');
  await page.locator('#trackList [data-track-index="2"]').click();
  await expect(page.locator('#audioStatus')).toHaveText('No audio');
});

test('fits Muppets animation timing to staged audio across multiple cycles', async ({ page }) => {
  const image = nativeRev6Fixture().png;
  const document = {
    rev: 6,
    source_format: 'muppets',
    source_revision: 2,
    sources: [{ src: 'dot.png', id: 0 }],
    anims: [{
      name: '01-kermit_01',
      width: 64,
      height: 64,
      layers: [{
        name: 'dot',
        type: 1,
        parent: -1,
        id: 0,
        src: 0,
        anchor_x: 0.5,
        anchor_y: 0.5,
        frames: [
          {
            time: 0,
            pos: { immediate: 0, x: 32, y: 32 },
            scale: { immediate: 0, x: 3200, y: 3200 },
            opacity: { immediate: 0, value: 100 },
            sprite: { immediate: -1, string: '' },
          },
          { time: 2 },
        ],
      }],
    }],
  };

  await page.goto('.');
  await page.locator('#fileInput').setInputFiles([
    { name: 'muppet_kermit_stage01.json', mimeType: 'application/json', buffer: Buffer.from(JSON.stringify(document)) },
    { name: 'dot.png', mimeType: 'image/png', buffer: image },
    { name: '01-kermit_01.wav', mimeType: 'audio/wav', buffer: silentWav(4.1) },
  ]);

  await expect(page.locator('#audioStatus')).toHaveText('01-kermit_01.wav');
  await expect(page.locator('#viewportPanel')).toHaveAttribute('data-audio-loop-multiplier', '2');
  const playbackSpeed = Number(await page.locator('#viewportPanel').getAttribute('data-animation-playback-speed'));
  expect(playbackSpeed).toBeCloseTo(4 / 4.1, 2);

  await page.locator('#playButton').click();
  await expect(page.locator('#viewportPanel')).toHaveAttribute('data-audio-loop-index', '1', { timeout: 3_000 });
  await page.locator('#playButton').click();
});

test('prefers explicit track audio sources and never falls back to unrelated clips', async ({ page }) => {
  const image = nativeRev6Fixture().png;
  const renderLayer = {
    name: 'dot',
    type: 1,
    parent: -1,
    id: 0,
    src: 0,
    anchor_x: 0.5,
    anchor_y: 0.5,
    frames: [{
      time: 0,
      pos: { immediate: 0, x: 32, y: 32 },
      scale: { immediate: 0, x: 3200, y: 3200 },
      opacity: { immediate: 0, value: 100 },
      sprite: { immediate: -1, string: '' },
    }],
  };
  const audioLayer = {
    name: 'audio',
    type: 2,
    parent: -1,
    id: 1,
    src: 1,
    anchor_x: 0,
    anchor_y: 0,
    frames: [],
  };
  const document = {
    rev: 6,
    source_format: 'rev6',
    source_revision: 6,
    sources: [
      { src: 'dot.png', id: 0 },
      { src: 'track_clip.ogg', id: 1 },
    ],
    anims: [
      { name: 'Performance', width: 64, height: 64, layers: [renderLayer, audioLayer] },
      { name: 'Monster A Activate', width: 64, height: 64, layers: [renderLayer] },
      { name: 'Idle', width: 64, height: 64, layers: [renderLayer, audioLayer] },
    ],
  };

  await page.goto('.');
  await page.locator('#fileInput').setInputFiles([
    { name: 'monster_a.json', mimeType: 'application/json', buffer: Buffer.from(JSON.stringify(document)) },
    { name: 'dot.png', mimeType: 'image/png', buffer: image },
    { name: 'track_clip.ogg', mimeType: 'audio/ogg', buffer: Buffer.from([1, 2, 3]) },
    { name: 'other_activate.ogg', mimeType: 'audio/ogg', buffer: Buffer.from([4, 5, 6]) },
  ]);

  await expect(page.locator('#audioStatus')).toHaveText('track_clip.ogg');
  await page.locator('#trackList [data-track-index="1"]').click();
  await expect(page.locator('#audioStatus')).toHaveText('No audio');
  await page.locator('#trackList [data-track-index="2"]').click();
  await expect(page.locator('#audioStatus')).toHaveText('No audio');
});

test('loads and animates a costume shader sequence texture', async ({ page }) => {
  const fixture = nativeRev6Fixture();
  const document = {
    rev: 6,
    sources: [{ src: 'atlas.xml', id: 0 }],
    anims: [{
      name: 'Idle',
      width: 64,
      height: 64,
      layers: [{
        name: 'sprite',
        parent: -1,
        id: 0,
        src: 0,
        anchor_x: 0.5,
        anchor_y: 0.5,
        frames: [
          {
            time: 0,
            pos: { immediate: 0, x: 32, y: 32 },
            scale: { immediate: 0, x: 3200, y: 3200 },
            opacity: { immediate: 0, value: 100 },
            sprite: { immediate: 1, string: 'dot' },
          },
          { time: 1 },
        ],
      }],
    }],
  };
  const costume = {
    apply_shader: [{ node: 'sprite', resource: 'ShaderCostumeA3' }],
    remaps: [],
    clone_layers: [],
    set_blend_layers: [],
    layer_colors: [],
    ae_anim_layers: [],
    sheet_remaps: [],
  };

  await page.goto('.');
  const sequence = await page.evaluate(async () => {
    const canvas = document.createElement('canvas');
    canvas.width = 7;
    canvas.height = 1;
    const context = canvas.getContext('2d')!;
    ['#ff0000', '#00ff00', '#0000ff', '#ffff00', '#ff00ff', '#00ffff', '#ffffff']
      .forEach((color, index) => {
        context.fillStyle = color;
        context.fillRect(index, 0, 1, 1);
      });
    const blob = await new Promise<Blob>((resolve) => canvas.toBlob((value) => resolve(value!), 'image/png'));
    return [...new Uint8Array(await blob.arrayBuffer())];
  });
  await page.locator('#fileInput').setInputFiles([
    { name: 'monster_test.json', mimeType: 'application/json', buffer: Buffer.from(JSON.stringify(document)) },
    { name: 'atlas.xml', mimeType: 'application/xml', buffer: Buffer.from(fixture.xml) },
    { name: 'atlas.png', mimeType: 'image/png', buffer: fixture.png },
    { name: 'costume_test_03.json', mimeType: 'application/json', buffer: Buffer.from(JSON.stringify(costume)) },
    { name: 'monster_test_costume_03_sequence.png', mimeType: 'image/png', buffer: Buffer.from(sequence) },
  ]);

  await expect(page.locator('#appearanceSelect option')).toHaveCount(2);
  await page.locator('#appearanceSelect').selectOption('costume:0');
  await expect(page.locator('#statusText')).toHaveText('Costume appearance applied');
  const renderAt = async (time: number): Promise<Buffer> => {
    await page.locator('#timeline').evaluate((element, value) => {
      element.value = String(value);
      element.dispatchEvent(new Event('input', { bubbles: true }));
    }, time);
    await page.waitForTimeout(30);
    return page.locator('.viewport-canvas').screenshot();
  };
  expect((await renderAt(0.02)).equals(await renderAt(0.04))).toBe(false);
});

test('attaches and animates a costume resource on its target layer', async ({ page }) => {
  const fixture = nativeRev6Fixture();
  const frame = (time: number, x?: number) => ({
    time,
    ...(x === undefined ? {} : {
      pos: { immediate: 1, x, y: 0 },
      scale: { immediate: 1, x: 100, y: 100 },
      opacity: { immediate: 1, value: 100 },
      sprite: { immediate: 1, string: 'dot' },
    }),
  });
  const baseDocument = {
    rev: 6,
    sources: [{ src: 'atlas.xml', id: 0 }],
    anims: [{
      name: 'Idle',
      width: 64,
      height: 64,
      layers: [{
        name: 'body',
        parent: -1,
        id: 0,
        src: 0,
        anchor_x: 0.5,
        anchor_y: 0.5,
        frames: [{
          time: 0,
          pos: { immediate: 1, x: 32, y: 32 },
          scale: { immediate: 1, x: 3200, y: 3200 },
          opacity: { immediate: 1, value: 100 },
          sprite: { immediate: 1, string: 'dot' },
        }, { time: 1 }],
      }],
    }],
  };
  const attachmentDocument = {
    rev: 6,
    sources: [{ src: 'attachment.xml', id: 0 }],
    anims: [{
      name: 'Flap',
      width: 64,
      height: 64,
      layers: [
        {
          name: 'root', parent: -1, id: 0, src: 0, anchor_x: 0, anchor_y: 0,
          frames: [frame(0, 0), frame(1)],
        },
        {
          name: 'ornament', parent: 0, id: 1, src: 0, anchor_x: 0.5, anchor_y: 0.5,
          frames: [frame(0, -12), frame(0.5, 12), frame(1, -12)],
        },
      ],
    }],
  };
  const costume = {
    apply_shader: [], remaps: [], clone_layers: [], set_blend_layers: [], layer_colors: [], sheet_remaps: [],
    ae_anim_layers: [{ attach_to: 'body', resource: 'monster_test_attachment.json', animation: 'Flap' }],
  };

  await page.goto('.');
  await page.locator('#fileInput').setInputFiles([
    { name: 'monster_test.json', mimeType: 'application/json', buffer: Buffer.from(JSON.stringify(baseDocument)) },
    { name: 'atlas.xml', mimeType: 'application/xml', buffer: Buffer.from(fixture.xml) },
    { name: 'atlas.png', mimeType: 'image/png', buffer: fixture.png },
    { name: 'costume_test_03.json', mimeType: 'application/json', buffer: Buffer.from(JSON.stringify(costume)) },
    { name: 'monster_test_attachment.json', mimeType: 'application/json', buffer: Buffer.from(JSON.stringify(attachmentDocument)) },
    { name: 'attachment.xml', mimeType: 'application/xml', buffer: Buffer.from(fixture.xml) },
  ]);

  await page.locator('#controlTab-files').click();
  await page.locator('#animationFileSelect').selectOption({ label: '[JSON] monster_test.json' });
  await expect(page.locator('#appearanceSelect option')).toHaveCount(2);
  await page.locator('#appearanceSelect').selectOption('costume:0');
  await expect(page.locator('#statusText')).toHaveText('Costume appearance applied');
  const renderAt = async (time: number): Promise<Buffer> => {
    await page.locator('#timeline').evaluate((element, value) => {
      element.value = String(value);
      element.dispatchEvent(new Event('input', { bubbles: true }));
    }, time);
    await page.waitForTimeout(30);
    return page.locator('.viewport-canvas').screenshot();
  };
  expect((await renderAt(0.1)).equals(await renderAt(0.6))).toBe(false);
});

test('browses a known DOF manifest without hosting bundle assets', async ({ page }) => {
  await page.goto('.');
  await page.locator('#dofGrabberButton').click();

  await expect(page.locator('#dofManifestStatus')).toContainText('Loaded 469 bundles');
  await expect(page.locator('#dofBundleCounts')).toContainText('458 visible');
  await page.locator('#dofBundleFilter').fill('tweedle');
  await expect(page.locator('.grabber-bundle-row')).toHaveCount(7);
  await expect(page.locator('.grabber-download-link').first()).toHaveAttribute(
    'href',
    'https://dlc2.bbbgame.net/monster_babies/dlc/00018241-2807c732a3da/ios/staticinfo_a_tweedle.unity3d',
  );

  await page.locator('#dofSelectVisible').click();
  await expect(page.locator('#dofBundleCounts')).toContainText('9 selected');
  await expect(page.locator('#dofDownloadChecked')).toBeEnabled();

  const bounds = await page.locator('.grabber-dialog').evaluate((dialog) => {
    const rect = dialog.getBoundingClientRect();
    return {
      left: rect.left,
      right: rect.right,
      top: rect.top,
      bottom: rect.bottom,
      viewportWidth: window.innerWidth,
      viewportHeight: window.innerHeight,
    };
  });
  expect(bounds.left).toBeGreaterThanOrEqual(0);
  expect(bounds.right).toBeLessThanOrEqual(bounds.viewportWidth);
  expect(bounds.top).toBeGreaterThanOrEqual(0);
  expect(bounds.bottom).toBeLessThanOrEqual(bounds.viewportHeight);
});

test('imports an arbitrary DOF bundles.json locally', async ({ page }) => {
  await page.goto('.');
  await page.locator('#dofGrabberButton').click();
  await expect(page.locator('#dofManifestStatus')).toContainText('Loaded 469 bundles');

  await page.locator('#dofBuildKeyInput').fill('custom-build');
  await page.locator('#dofPlatformSelect').selectOption('android');
  await page.locator('#dofManifestInput').setInputFiles({
    name: 'bundles.json',
    mimeType: 'application/json',
    buffer: Buffer.from(JSON.stringify({
      'staticinfo_custom.unity3d': { size: 1234 },
      'sounds.unity3d': { size: 4321 },
    })),
  });

  await expect(page.locator('#dofManifestStatus')).toHaveText('Loaded 2 bundles from bundles.json');
  await expect(page.locator('.grabber-bundle-row')).toHaveCount(1);
  await page.locator('#dofStaticinfoOnly').uncheck();
  await expect(page.locator('.grabber-bundle-row')).toHaveCount(2);
  await expect(page.locator('.grabber-download-link').first()).toHaveAttribute(
    'href',
    'https://dlc2.bbbgame.net/monster_babies/dlc/custom-build/android/sounds.unity3d',
  );
});

test('imports a completed grabber download through an authorized Downloads folder', async ({ page }, testInfo) => {
  test.skip(testInfo.project.name !== 'desktop-chromium');
  await page.addInitScript(() => {
    const downloaded: File[] = [];
    Object.defineProperty(window, 'showDirectoryPicker', {
      configurable: true,
      value: async () => ({
        kind: 'directory',
        name: 'Downloads',
        queryPermission: async () => 'granted',
        async *values() {
          for (const file of downloaded) {
            yield {
              kind: 'file',
              name: file.name,
              getFile: async () => file,
            };
          }
        },
      }),
    });
    const nativeClick = HTMLAnchorElement.prototype.click;
    HTMLAnchorElement.prototype.click = function click() {
      if (new URL(this.href).pathname.endsWith('/particles.unity3d')) {
        downloaded.push(new File(
          [new Uint8Array([1, 2, 3, 4])],
          'particles (1).unity3d',
          { type: 'application/octet-stream', lastModified: Date.now() },
        ));
        return;
      }
      nativeClick.call(this);
    };
  });

  await page.goto('.');
  await page.locator('#dofGrabberButton').click();
  await expect(page.locator('#dofManifestStatus')).toContainText('Loaded 469 bundles');
  await page.locator('#dofManifestInput').setInputFiles({
    name: 'bundles.json',
    mimeType: 'application/json',
    buffer: Buffer.from(JSON.stringify({ 'particles.unity3d': { size: 4 } })),
  });
  await page.locator('#dofStaticinfoOnly').uncheck();
  await page.locator('#dofSelectVisible').click();

  await expect(page.locator('#dofDownloadChecked')).toHaveAttribute('data-import-mode', 'automatic');
  await expect(page.locator('#dofDownloadChecked')).toContainText('Download + import');
  await page.locator('#dofDownloadChecked').click();

  await expect(page.locator('#dofGrabberBackdrop')).toBeHidden();
  await expect(page.locator('#fileCount')).toHaveText('1 file');
  await expect(page.locator('#statusText')).toHaveText('1 asset indexed; no animation BIN or JSON found');
});

test('extracts a real DOF Unity bundle locally when a fixture is available', async ({ page }, testInfo) => {
  const bundlePath = process.env.ANIVIEWER_DOF_BUNDLE;
  const variantPath = process.env.ANIVIEWER_DOF_VARIANT;
  const staticDataPath = process.env.ANIVIEWER_DOF_STATIC_DATA;
  const audioBundlePath = process.env.ANIVIEWER_DOF_AUDIO_BUNDLE;
  test.skip(testInfo.project.name !== 'desktop-chromium' || !bundlePath || !existsSync(bundlePath));
  testInfo.setTimeout(180_000);

  const pageErrors: string[] = [];
  const consoleMessages: string[] = [];
  page.on('pageerror', (error) => pageErrors.push(error.message));
  page.on('console', (message) => consoleMessages.push(message.text()));
  await page.goto('.');
  await page.locator('#fileInput').setInputFiles([
    bundlePath!,
    ...(variantPath && existsSync(variantPath) ? [variantPath] : []),
    ...(staticDataPath && existsSync(staticDataPath) ? [staticDataPath] : []),
    ...(audioBundlePath && existsSync(audioBundlePath) ? [audioBundlePath] : []),
  ]);

  await expect(page.locator('#revisionValue')).toHaveText('DOF Unity', { timeout: 120_000 });
  await expect(page.locator('#trackList [data-track-index]').first()).toBeVisible();
  await expect(page.locator('#layerList [data-layer-id]').first()).toBeVisible();
  await expect(page.locator('#viewportEmpty')).toBeHidden();
  await expect(page.locator('#dofStageSelect')).toBeVisible();
  await expect(page.locator('#trackList [data-track-index]')).toHaveCount(9);
  await page.locator('#dofStageSelect').selectOption('adult');
  await expect(page.locator('#trackList [data-track-index]')).toHaveCount(3);
  await page.locator('#dofStageSelect').selectOption('baby');
  await expect(page.locator('#trackList [data-track-index]')).toHaveCount(6);
  if (variantPath && existsSync(variantPath)) {
    await expect(page.locator('#appearanceSelect option').first()).toHaveText('Base');
    await expect(page.locator('#appearanceSelect option')).toHaveCount(2);
  }
  if (audioBundlePath && existsSync(audioBundlePath)) {
    await page.locator('#dofStageSelect').selectOption('adult');
    await expect(page.locator('#audioButton')).toBeEnabled();
    await expect(page.locator('#audioStatus')).not.toHaveText('No audio');
  }

  await page.locator('#dofStageSelect').selectOption('baby');
  const babyIdleRow = page.locator('#trackList [data-track-index]').filter({
    has: page.locator('.row-name', { hasText: /^A_tweedle_baby_idle$/ }),
  });
  await babyIdleRow.click();
  const layerRows = page.locator('#layerList [data-layer-id]');
  for (let index = 0; index < await layerRows.count(); index += 1) {
    const row = layerRows.nth(index);
    if ((await row.locator('.row-name').textContent())?.trim() !== 'Sprite2D_right_wing') {
      await row.locator('.visibility-button').click();
    }
  }
  const renderAt = async (time: number): Promise<Buffer> => {
    await page.locator('#timeline').evaluate((element, value) => {
      element.value = String(value);
      element.dispatchEvent(new Event('input', { bubbles: true }));
    }, time);
    await page.waitForTimeout(50);
    return page.locator('.viewport-canvas').screenshot();
  };
  const firstWingFrame = await renderAt(0.267);
  const secondWingFrame = await renderAt(0.334);
  expect(firstWingFrame.equals(secondWingFrame)).toBe(false);
  if (variantPath && existsSync(variantPath)) {
    await page.locator('#appearanceSelect').selectOption({ index: 1 });
    const idleRow = page.locator('#trackList [data-track-index]').filter({
      has: page.locator('.row-name', { hasText: /_idle$/ }),
    });
    await idleRow.click();
    await expect(page.locator('#layerList [data-shader-name="Anim2D/ShaderGradientMap+Alpha"]').first()).toBeVisible();
    await expect(page.locator('#viewportEmpty')).toBeHidden();
    if (staticDataPath && existsSync(staticDataPath)) {
      expect(consoleMessages.some((message) => message.includes('[DOF chromatic] Applied static_data'))).toBe(true);
    }
  }
  expect(pageErrors).toEqual([]);
});

test('extracts and renders real DOF particle prefabs when fixtures are available', async ({ page }, testInfo) => {
  const monsterBundlePath = process.env.ANIVIEWER_DOF_PARTICLE_MONSTER_BUNDLE;
  const particleBundlePath = process.env.ANIVIEWER_DOF_PARTICLE_BUNDLE;
  test.skip(
    testInfo.project.name !== 'desktop-chromium'
      || !monsterBundlePath
      || !particleBundlePath
      || !existsSync(monsterBundlePath)
      || !existsSync(particleBundlePath),
  );
  testInfo.setTimeout(180_000);

  const pageErrors: string[] = [];
  const particleWarnings: string[] = [];
  page.on('pageerror', (error) => pageErrors.push(error.message));
  page.on('console', (message) => {
    if (message.type() === 'warning' && message.text().startsWith('[DOF particles]')) {
      particleWarnings.push(message.text());
    }
  });
  await page.goto('.');
  await page.locator('#fileInput').setInputFiles(monsterBundlePath!);
  await expect(page.locator('#revisionValue')).toHaveText('DOF Unity', { timeout: 120_000 });
  await expect(page.locator('#viewportPanel')).toHaveAttribute('data-particle-prefab-count', '0');
  await expect(page.locator('#statusText')).toContainText('add particles.unity3d');
  await page.locator('#fileInput').setInputFiles(particleBundlePath!);
  await expect(page.locator('#viewportPanel')).toHaveAttribute('data-particle-prefab-count', '3', { timeout: 120_000 });
  await page.locator('#dofStageSelect').selectOption('adult');
  const sleepRow = page.locator('#trackList [data-track-index]').filter({
    has: page.locator('.row-name', { hasText: /^F_kano_adult_sleep$/ }),
  });
  await expect(sleepRow).toBeVisible();
  await sleepRow.click();
  await expect(page.locator('#viewportPanel')).toHaveAttribute('data-particle-prefab-count', '3');
  await expect(page.locator('#viewportPanel')).toHaveAttribute('data-particle-emitter-count', '3');
  await page.locator('#layerList [data-layer-id]:not(.hidden-layer) .visibility-button').evaluateAll(
    (buttons) => buttons.forEach((button) => (button as HTMLButtonElement).click()),
  );
  const renderAt = async (time: number): Promise<Buffer> => {
    await page.locator('#timeline').evaluate((element, value) => {
      element.value = String(value);
      element.dispatchEvent(new Event('input', { bubbles: true }));
    }, time);
    await page.waitForTimeout(50);
    return page.locator('.viewport-canvas').screenshot();
  };
  const firstFrame = await renderAt(0.6);
  expect(Number(await page.locator('#viewportPanel').getAttribute('data-active-particle-count'))).toBeGreaterThan(0);
  const secondFrame = await renderAt(1.6);
  expect(firstFrame.equals(secondFrame)).toBe(false);
  expect(particleWarnings).toEqual([]);
  expect(pageErrors).toEqual([]);
});
