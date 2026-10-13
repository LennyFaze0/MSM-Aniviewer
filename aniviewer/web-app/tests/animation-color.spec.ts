import { expect, test } from '@playwright/test';

for (const scenario of ['legacy-tinted-glow', 'modern-alpha', 'static-layer-color'] as const) {
  test(`renders and exports ${scenario} with the same colors`, async ({ page }) => {
    await page.goto('.');
    const samples = await page.evaluate(async (mode) => {
      const { AnimationRenderer } = await import('/MSM-Aniviewer/src/renderer/animation-renderer.ts');
      const host = document.createElement('div');
      host.style.cssText = 'position:fixed;inset:0;width:200px;height:200px;z-index:10000';
      document.body.appendChild(host);
      const renderer = new AnimationRenderer(host);
      await renderer.initialize();
      const layer = (id: number, blend: number, color: number[], opacity = 100) => ({
        name: `layer ${id}`, id, blend, src: 0, parent: -1, anchor_x: 0.5, anchor_y: 0.5,
        frames: [{
          time: 0,
          pos: { immediate: 1, x: 32, y: 32 },
          scale: { immediate: 1, x: 3200, y: 3200 },
          opacity: { immediate: 1, value: opacity },
          sprite: { immediate: 1, string: 'white' },
          rgb: { immediate: 1, red: color[0], green: color[1], blue: color[2] },
        }],
      });
      // Rare Ghazt's head uses this tint, 20% opacity and raw blend 1.
      const foreground = layer(0, 1, [246, 253, 41], 20);
      const background = layer(1, 0, [40, 60, 80]);
      if (mode === 'static-layer-color') {
        Object.assign(foreground, { rgb: { red: 246, green: 253, blue: 41 }, blend: 0 });
        foreground.frames[0].rgb = { immediate: -1, red: 255, green: 255, blue: 255 };
        foreground.frames[0].opacity.value = 100;
      }
      const track = { name: 'Recolor', width: 64, height: 64, layers: [foreground, background] };
      const animation = { rev: 6, blend_version: 2, sources: [{ src: 'white', id: 0 }], anims: [track] };
      if (mode === 'modern-alpha') {
        // Mode detection must inspect inactive tracks as the desktop does.
        animation.anims.push({ ...track, name: 'Modern marker', layers: [layer(2, 2, [255, 255, 255])] });
      }
      const image = await createImageBitmap(new ImageData(new Uint8ClampedArray([255, 255, 255, 255]), 1, 1));
      renderer.load(animation, [{
        sourceId: 0, image, alphaMode: 'premultiplied-alpha', sourceAlphaFormat: 'opaque',
        data: {
          imagePath: 'white', width: 1, height: 1, hires: false,
          sprites: new Map([['white', {
            name: 'white', x: 0, y: 0, width: 1, height: 1, originalWidth: 1, originalHeight: 1,
            offsetX: 0, offsetY: 0, rotated: false,
          }]]),
        },
      }]);
      const sample = (source: HTMLCanvasElement | OffscreenCanvas): number[] => {
        const canvas = document.createElement('canvas');
        canvas.width = source.width;
        canvas.height = source.height;
        const context = canvas.getContext('2d')!;
        context.drawImage(source, 0, 0);
        return [...context.getImageData(canvas.width / 2, canvas.height / 2, 1, 1).data];
      };
      renderer.application.render();
      const viewport = sample(renderer.application.canvas);
      const exported = await renderer.withExportSession(async (session) => sample(session.captureCanvas({
        time: 0, bounds: { x: 0, y: 0, width: 64, height: 64 },
      })));
      renderer.application.ticker.stop();
      return { viewport, exported };
    }, scenario);

    const expected = scenario === 'legacy-tinted-glow' ? [89, 111, 88, 255]
      : scenario === 'modern-alpha' ? [81, 99, 72, 255]
        : [246, 253, 41, 255];
    for (const sample of [samples.viewport, samples.exported]) {
      sample.forEach((channel, index) => expect(Math.abs(channel - expected[index]!)).toBeLessThanOrEqual(2));
    }
  });
}
