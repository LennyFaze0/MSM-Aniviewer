import { chromium, expect, test, type Page } from '@playwright/test';
import { readFileSync } from 'node:fs';

function toneWav(duration: number): Buffer {
  const rate = 8000;
  const samples = Math.round(duration * rate);
  const wav = Buffer.alloc(44 + samples * 2);
  wav.write('RIFF'); wav.writeUInt32LE(wav.length - 8, 4); wav.write('WAVEfmt ', 8);
  wav.writeUInt32LE(16, 16); wav.writeUInt16LE(1, 20); wav.writeUInt16LE(1, 22);
  wav.writeUInt32LE(rate, 24); wav.writeUInt32LE(rate * 2, 28); wav.writeUInt16LE(2, 32); wav.writeUInt16LE(16, 34);
  wav.write('data', 36); wav.writeUInt32LE(samples * 2, 40);
  for (let i = 0; i < samples; i++) wav.writeInt16LE(Math.round(Math.sin(i * 440 * Math.PI * 2 / rate) * 5000), 44 + i * 2);
  return wav;
}

async function loadFixture(page: Page, { duration = 1.05, audio = false, muppets = false, size = 0, layers = 1 } = {}) {
  const document = {
    rev: 6, source_format: muppets ? 'muppets' : 'rev6', sources: [{ src: 'atlas.xml', id: 0 }],
    anims: [{ name: '01_Export_Test', width: size || 65, height: size || 63, layers: Array.from({ length: layers }, (_, id) => ({
      name: `sprite_${id}`, parent: -1, id, src: 0, anchor_x: .5, anchor_y: .5,
      frames: [{ time: 0, pos: { immediate: 0, x: 20 + id % 8 * 55, y: 32 + Math.floor(id / 8) * 55 }, scale: { immediate: 0, x: 50, y: 50 },
        opacity: { immediate: 0, value: 100 }, sprite: { immediate: 0, string: 'dot' } },
      { time: duration, pos: { immediate: 0, x: 40 + id % 8 * 55, y: 32 + Math.floor(id / 8) * 55 } }],
    })) }],
  };
  await page.goto('.');
  const png = Buffer.from(await page.evaluate(() => {
    const canvas = document.createElement('canvas'); canvas.width = 32; canvas.height = 32;
    const context = canvas.getContext('2d')!;
    const gradient = context.createLinearGradient(0, 0, 32, 32);
    gradient.addColorStop(0, '#ff8800'); gradient.addColorStop(.5, '#ee3300'); gradient.addColorStop(1, '#0022ff');
    context.fillStyle = gradient; context.fillRect(0, 0, 32, 32);
    return canvas.toDataURL('image/png').split(',')[1]!;
  }), 'base64');
  await page.locator('#fileInput').setInputFiles([
    { name: 'export_fixture.json', mimeType: 'application/json', buffer: Buffer.from(JSON.stringify(document)) },
    { name: 'atlas.xml', mimeType: 'application/xml', buffer: Buffer.from('<TextureAtlas imagePath="atlas.png" width="32" height="32"><sprite n="dot" x="0" y="0" w="32" h="32" oW="32" oH="32"/></TextureAtlas>') },
    { name: 'atlas.png', mimeType: 'image/png', buffer: png },
    ...(audio ? [{ name: '01_Export_Test.wav', mimeType: 'audio/wav', buffer: toneWav(muppets ? duration * 2.05 : duration) }] : []),
  ]);
  if (audio) await expect(page.locator('#audioStatus')).toHaveText('01_export_test.wav');
  await page.locator('#controlTab-export').click();
  await page.locator('#exportFpsInput').fill('10');
  await page.locator('#exportFpsInput').dispatchEvent('change');
}

test('exports actual WebM with audio, exact timestamps, and unchanged viewport position', async ({ page, browserName }, info) => {
  await loadFixture(page, { audio: true });
  await page.locator('#exportVideoFormat').selectOption('webm');
  const referenceDownload = page.waitForEvent('download');
  await page.locator('#exportFrameButton').click();
  const referencePng = readFileSync((await (await referenceDownload).path())!);
  await page.locator('#timeline').evaluate((element: HTMLInputElement) => { element.value = '.5'; element.dispatchEvent(new Event('input')); });
  const downloadPromise = page.waitForEvent('download');
  await page.locator('#exportVideoButton').click();
  const download = await downloadPromise;
  expect(download.suggestedFilename()).toBe('01_Export_Test.webm');
  const path = info.outputPath('export.webm');
  await download.saveAs(path);
  const bytes = readFileSync(path);
  expect(bytes.subarray(0, 4).toString('hex')).toBe('1a45dfa3');
  await expect(page.locator('#timeline')).toHaveValue('0.5');
  await expect(page.locator('#exportProgressText')).toContainText('11 frames, with audio');
  const details = await page.evaluate(async ({ base64, reference, checkPixels }) => {
    const { Input, BufferSource, ALL_FORMATS, EncodedPacketSink, AudioSampleSink, CanvasSink } = await import('/MSM-Aniviewer/node_modules/mediabunny/dist/modules/src/index.js');
    const input = new Input({ source: new BufferSource(Uint8Array.from(atob(base64), (c) => c.charCodeAt(0))), formats: ALL_FORMATS });
    try {
      const video = (await input.getPrimaryVideoTrack())!;
      const audio = (await input.getPrimaryAudioTrack())!;
      const times: number[] = [];
      for await (const packet of new EncodedPacketSink(video).packets()) times.push(packet.timestamp);
      let maximum = 0;
      for await (const sample of new AudioSampleSink(audio).samples()) {
        const data = new Float32Array(sample.numberOfFrames * sample.numberOfChannels);
        sample.copyTo(data, { planeIndex: 0, format: 'f32' });
        for (const value of data) maximum = Math.max(maximum, Math.abs(value));
        sample.close();
      }
      const sink = new CanvasSink(video);
      const positions = [];
      function measure(canvas: HTMLCanvasElement | OffscreenCanvas) {
        const pixels = canvas.getContext('2d')!.getImageData(0, 0, canvas.width, canvas.height).data;
        let count = 0, sumX = 0;
        for (let i = 0; i < pixels.length; i += 4) {
          if (pixels[i]! > 80 && pixels[i]! > pixels[i + 1]! * 1.5) { count++; sumX += (i / 4) % canvas.width; }
        }
        return { count, x: sumX / count };
      }
      const referenceImage = new Image(); referenceImage.src = `data:image/png;base64,${reference}`;
      await referenceImage.decode();
      const referenceCanvas = document.createElement('canvas');
      referenceCanvas.width = referenceImage.width; referenceCanvas.height = referenceImage.height;
      referenceCanvas.getContext('2d')!.drawImage(referenceImage, 0, 0);
      // Linux WebKit's VP9 decoder rejects these valid exports; independently verified with FFmpeg.
      // Chromium additionally checks decoded pixels against the renderer's PNG reference.
      for (const time of checkPixels ? [0, 1] : []) {
        const frame = (await sink.getCanvas(time))!;
        positions.push(measure(frame.canvas));
      }
      return { duration: await input.getDurationFromMetadata(), width: video.displayWidth, height: video.displayHeight, times, maximum, positions, reference: measure(referenceCanvas) };
    } finally { input.dispose(); }
  }, { base64: bytes.toString('base64'), reference: referencePng.toString('base64'), checkPixels: browserName !== 'webkit' });
  expect(details.width).toBe(66); expect(details.height).toBe(64);
  // Opus packets may pad the final audio sample by less than 20 ms.
  expect(Math.abs(details.duration! - 1.05)).toBeLessThan(.021);
  expect(details.times).toHaveLength(11);
  for (let index = 0; index < details.times.length; index++) expect(details.times[index]).toBeCloseTo(index / 10, 3);
  expect(details.maximum).toBeGreaterThan(.05);
  if (details.positions.length) {
    expect(details.positions[0]!.count).toBeGreaterThan(50);
    expect(Math.abs(details.positions[0]!.x - details.reference.x)).toBeLessThan(1);
    expect(details.positions[1]!.x - details.positions[0]!.x).toBeGreaterThan(17);
  }
});

test('auto exports a complete Muppets audio cycle and reports its actual format', async ({ page }, info) => {
  await loadFixture(page, { duration: 1, audio: true, muppets: true });
  const downloadPromise = page.waitForEvent('download');
  await page.locator('#exportVideoButton').click();
  const download = await downloadPromise;
  await download.saveAs(info.outputPath(download.suggestedFilename()));
  expect(download.suggestedFilename()).toMatch(/\.(mp4|webm)$/);
  await expect(page.locator('#exportProgressText')).toContainText('21 frames, with audio');
});

test('silent WebM exports never use PNG compression', async ({ page }, info) => {
  await loadFixture(page, { audio: true });
  await page.locator('#exportVideoAudio').uncheck();
  await page.locator('#exportVideoFormat').selectOption('webm');
  await page.evaluate(() => {
    HTMLCanvasElement.prototype.toBlob = () => { throw new Error('Video must not encode PNGs'); };
    OffscreenCanvas.prototype.convertToBlob = () => { throw new Error('Video must not encode PNGs'); };
  });
  const downloadPromise = page.waitForEvent('download');
  await page.locator('#exportVideoButton').click();
  const download = await downloadPromise;
  await download.saveAs(info.outputPath('silent.webm'));
  await expect(page.locator('#exportProgressText')).toContainText('11 frames, no audio');
});

test('cancellation terminates a stalled worker and restores the export UI', async ({ page }) => {
  await page.addInitScript(() => {
    const Original = Worker;
    window.Worker = class extends Original {
      postMessage(message: unknown, transfer: Transferable[]) {
        if ((message as { type?: string }).type !== 'init') super.postMessage(message, transfer);
      }
    } as typeof Worker;
  });
  await loadFixture(page);
  await page.locator('#exportVideoButton').click();
  await expect(page.locator('#exportProgressText')).toContainText('Checking browser encoders');
  await page.locator('#exportCancelButton').click();
  await expect(page.locator('#statusText')).toHaveText('Export cancelled');
  await expect(page.locator('#exportVideoButton')).toBeEnabled();
  await expect(page.locator('#exportFrameButton')).toBeEnabled();
  await expect(page.locator('#timeline')).toHaveValue('0');
});

test('missing WebCodecs uses compatibility recording rather than a dead export button', async ({ page }, info) => {
  await page.addInitScript(() => { Object.defineProperty(window, 'VideoEncoder', { value: undefined }); });
  await loadFixture(page, { duration: .6, audio: true });
  await expect(page.locator('#exportVideoButton')).toBeEnabled();
  await expect(page.locator('#exportVideoHint')).toContainText('real-time recorder');
  await expect(page.locator('#exportFrameButton')).toBeEnabled();
  if (!await page.evaluate(() => typeof MediaRecorder !== 'undefined')) {
    await page.locator('#exportVideoButton').click();
    await expect(page.locator('#statusText')).toContainText('cannot record');
    return; // Linux WebKit omits MediaRecorder; the real insecure-origin test covers the recorder path.
  }
  const downloadPromise = page.waitForEvent('download');
  await page.locator('#exportVideoButton').click();
  const download = await downloadPromise;
  await download.saveAs(info.outputPath(download.suggestedFilename()));
  await expect(page.locator('#exportResult')).toBeVisible();
  await expect(page.locator('#exportProgressText')).toContainText('with audio');
});

test('blocked automatic download still permits a fresh Save or Share tap', async ({ page }, info) => {
  await page.addInitScript(() => {
    document.addEventListener('click', (event) => {
      if ((event.target as Element).closest('#saveExportButton') && !event.isTrusted) event.preventDefault();
    }, true);
    Object.defineProperty(navigator, 'canShare', { value: () => true });
    Object.defineProperty(navigator, 'share', { value: (data: ShareData) => {
      Object.assign(window, { sharedExport: { name: data.files?.[0]?.name, activeGesture: navigator.userActivation.isActive } });
      return Promise.resolve();
    } });
  });
  await loadFixture(page, { duration: .4 });
  await page.locator('#exportGifButton').click();
  await expect(page.locator('#statusText')).toContainText('GIF ready');
  const url = await page.locator('#saveExportButton').getAttribute('href');
  expect(await page.evaluate(async (url) => (await fetch(url!)).ok, url)).toBe(true);
  const downloadPromise = page.waitForEvent('download');
  await page.locator('#saveExportButton').click();
  const download = await downloadPromise;
  expect(download.suggestedFilename()).toBe('01_Export_Test.gif');
  await download.saveAs(info.outputPath('export.gif'));
  const gif = readFileSync((await download.path())!);
  expect(gif.subarray(0, 6).toString()).toBe('GIF89a');
  expect(gif.readUInt16LE(6)).toBe(65); expect(gif.readUInt16LE(8)).toBe(63);
  await page.locator('#shareExportButton').click();
  expect(await page.evaluate(() => (window as unknown as { sharedExport: unknown }).sharedExport)).toEqual({ name: '01_Export_Test.gif', activeGesture: true });
  await page.locator('#clearExportButton').click();
  await expect(page.locator('#exportResult')).toBeHidden();
  expect(await page.evaluate(async (url) => { try { await fetch(url!); return true; } catch { return false; } }, url)).toBe(false);
});

for (const format of ['mp4', 'mov'] as const) {
  test(`exports a real ${format.toUpperCase()} container with H.264 and audio`, async ({ page }, info) => {
    await loadFixture(page, { duration: .6, audio: true });
    await page.locator('#exportVideoFormat').selectOption(format);
    const downloadPromise = page.waitForEvent('download');
    await page.locator('#exportVideoButton').click();
    const download = await downloadPromise;
    await download.saveAs(info.outputPath(`export.${format}`));
    expect(download.suggestedFilename()).toBe(`01_Export_Test.${format}`);
    const bytes = readFileSync((await download.path())!);
    expect(bytes.subarray(4, 8).toString()).toBe('ftyp');
    expect(bytes.includes(Buffer.from(format === 'mov' ? 'qt  ' : 'isom'))).toBe(true);
    await expect(page.locator('#exportProgressText')).toContainText('6 frames, with audio');
  });
}

test('unsupported codecs produce a visible error instead of a silent tap', async ({ page }) => {
  await page.addInitScript(() => {
    Object.defineProperty(window, 'VideoEncoder', { value: undefined });
    if (typeof MediaRecorder !== 'undefined') MediaRecorder.isTypeSupported = () => false;
  });
  await loadFixture(page);
  await page.locator('#exportVideoButton').click();
  await expect(page.locator('#statusText')).toContainText('cannot record');
  await expect(page.locator('#exportVideoButton')).toBeEnabled();
});

test('real insecure HTTP origin can export through the mobile compatibility path', async ({}, info) => {
  test.skip(info.project.name !== 'desktop-chromium', 'One dedicated insecure-origin Chromium launch.');
  const browser = await chromium.launch({ args: ['--host-resolver-rules=MAP aniviewer.test 127.0.0.1', '--no-proxy-server'] });
  try {
    const page = await browser.newPage({ baseURL: 'http://aniviewer.test:4173/MSM-Aniviewer/', viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true });
    await loadFixture(page, { duration: .6, audio: true });
    expect(await page.evaluate(() => isSecureContext)).toBe(false);
    expect(await page.evaluate(() => typeof VideoEncoder)).toBe('undefined');
    const downloadPromise = page.waitForEvent('download');
    await page.locator('#exportVideoButton').click();
    const download = await downloadPromise;
    await download.saveAs(info.outputPath(download.suggestedFilename()));
    await expect(page.locator('#exportResult')).toBeVisible();
    await expect(page.locator('#exportProgressText')).toContainText('with audio');
  } finally { await browser.close(); }
});

test('optional same-renderer PNG versus native video throughput benchmark', async ({ page }, info) => {
  test.skip(!process.env.ANIVIEWER_EXPORT_BENCHMARK, 'Opt-in measurement, not a machine-dependent performance gate.');
  test.setTimeout(120_000);
  await loadFixture(page, { duration: 4, size: 512, layers: 64 });
  await page.locator('#exportFpsInput').fill('30');
  await page.locator('#exportFpsInput').dispatchEvent('change');
  const results = [];
  for (const [button, filename] of [['#exportSequenceButton', 'sequence.zip'], ['#exportVideoButton', 'video.webm']]) {
    const started = Date.now();
    const downloadPromise = page.waitForEvent('download');
    await page.locator(button).click();
    const download = await downloadPromise;
    await download.saveAs(info.outputPath(filename));
    results.push({ format: download.suggestedFilename(), milliseconds: Date.now() - started, bytes: readFileSync(info.outputPath(filename)).length });
  }
  console.log('EXPORT_BENCHMARK', JSON.stringify({ width: 512, height: 512, frames: 120, layers: 64, results }));
  await info.attach('timings', { body: JSON.stringify(results, null, 2), contentType: 'application/json' });
});
