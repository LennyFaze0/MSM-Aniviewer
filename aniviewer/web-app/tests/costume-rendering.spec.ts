import { expect, test } from '@playwright/test';

test('costume atlas routing and frame-swap draw offsets agree in viewport and export', async ({ page }) => {
  await page.goto('.');
  const result = await page.evaluate(async () => {
    const { AnimationRenderer } = await import('/MSM-Aniviewer/src/renderer/animation-renderer.ts');
    const { applyCostumeDefinition } = await import('/MSM-Aniviewer/src/core/costume.ts');
    const { calculateWorldStates } = await import('/MSM-Aniviewer/src/core/animation.ts');
    const { parseTextureAtlas } = await import('/MSM-Aniviewer/src/core/atlas.ts');
    const defaults = parseTextureAtlas('<TextureAtlas imagePath="test.png" width="40" height="40"><sprite n="default" w="20" h="30" /></TextureAtlas>').sprites.get('default')!;
    const host = document.createElement('div'); host.style.cssText = 'position:fixed;inset:0;width:300px;height:300px'; document.body.append(host);
    const renderer = new AnimationRenderer(host); await renderer.initialize();
    const makeAtlas = async (sourceId: number, name: string, color: string) => {
      const image = document.createElement('canvas'); image.width = image.height = 40;
      const ctx = image.getContext('2d')!; ctx.fillStyle = color; ctx.fillRect(0, 0, 40, 40);
      return { sourceId, sourceName: name, image, alphaMode: 'premultiplied-alpha', sourceAlphaFormat: 'opaque',
        data: parseTextureAtlas(`<TextureAtlas imagePath="${name}" width="40" height="40"><sprite n="coat1" w="40" h="40" pX="0.2" pY="0.7"/><sprite n="coat2" w="40" h="40"/><sprite n="child" w="4" h="4"/></TextureAtlas>`) };
    };
    const wrong = await makeAtlas(0, 'wrong.xml', '#ff0000'), correct = await makeAtlas(1, 'costume.xml', '#00ff00');
    const body = { id: 0, name: 'body', parent: -1, src: 0, anchor_x: 10, anchor_y: 12,
      frames: [{ time: 0, sprite: { immediate: 1, string: 'base1' }, pos: { immediate: 0, x: 40, y: 40 } },
        { time: 1, sprite: { immediate: 1, string: 'base2' } }, { time: 2 }] };
    const child = { ...body, id: 1, name: 'child', parent: 0, anchor_x: 0, anchor_y: 0,
      frames: [{ time: 0, sprite: { immediate: 1, string: 'child' }, pos: { immediate: 0, x: 90, y: 90 } }] };
    const base = { rev: 6, sources: [], anims: [{ name: 'Costume', width: 160, height: 160, layers: [body, child] }] };
    const output = applyCostumeDefinition(base, { remaps: [{ display_name: 'body', sheet: 'costume.xml', resource: 'coat1',
      frame_mappings: [{ from: 'base1', to: 'coat1' }, { from: 'base2', to: 'coat2' }] }],
      clone_layers: [], apply_shader: [], set_blend_layers: [], layer_colors: [], ae_anim_layers: [], sheet_remaps: [] }, [0, 1], [wrong, correct]);
    renderer.load(output, [wrong, correct]); renderer.application.ticker.stop();
    const first = renderer.layerViewGeometry(0)!.corners;
    renderer.setTime(1); const second = renderer.layerViewGeometry(0)!.corners;
    const pixels = await renderer.withExportSession(async (session) => {
      const canvas = session.captureCanvas({ time: 1, bounds: { x: 0, y: 0, width: 160, height: 160 } });
      const read = document.createElement('canvas'); read.width = read.height = 160;
      const ctx = read.getContext('2d')!; ctx.drawImage(canvas, 0, 0);
      return [...ctx.getImageData(40, 25, 1, 1).data];
    });
    return { first, second, pixels, defaults,
      childBefore: calculateWorldStates(base.anims[0], 1).get(1), childAfter: calculateWorldStates(output.anims[0], 1).get(1) };
  });
  expect(result.defaults).toMatchObject({ pivotX: .5, pivotY: .5, originalWidth: 20, originalHeight: 30 });
  expect(result.first).toEqual(result.second);
  expect(result.childBefore).toEqual(result.childAfter);
  expect(result.pixels).toEqual([0, 255, 0, 255]);
});

test('sheet-projected costume shaders sample authored offsets, not equal strip slots', async ({ page }) => {
  await page.goto('.');
  const pixels = await page.evaluate(async () => {
    const { AnimationRenderer } = await import('/MSM-Aniviewer/src/renderer/animation-renderer.ts');
    const { parseTextureAtlas } = await import('/MSM-Aniviewer/src/core/atlas.ts');
    const host = document.createElement('div'); host.style.cssText = 'position:fixed;inset:0;width:300px;height:300px'; document.body.append(host);
    const renderer = new AnimationRenderer(host); await renderer.initialize();
    const atlasImage = document.createElement('canvas'); atlasImage.width = atlasImage.height = 512;
    const sequence = document.createElement('canvas'); sequence.width = 64; sequence.height = 128;
    const ctx = sequence.getContext('2d')!;
    ['#ff0000', '#00ff00', '#0000ff', '#ffffff'].forEach((color, i) => { ctx.fillStyle = color; ctx.fillRect(0, i * 26, 64, 26); });
    renderer.load({ rev: 6, sources: [], anims: [{ name: 'Shader', width: 128, height: 52, layers: [{
      name: 'body', id: 0, src: 0, parent: -1, anchor_x: 0, anchor_y: 0, shader_name: 'ShaderCostumeE9',
      frames: [{ time: 0, sprite: { immediate: 1, string: 'body' } }, { time: 1 }],
    }] }] }, [{ sourceId: 0, image: atlasImage, alphaMode: 'premultiplied-alpha', sourceAlphaFormat: 'opaque',
      data: parseTextureAtlas('<TextureAtlas imagePath="base.png" width="512" height="512"><sprite n="body" x="335" y="188" w="128" h="52"/></TextureAtlas>'),
    }], [{ shaderName: 'ShaderCostumeE9', image: sequence, alphaMode: 'premultiplied-alpha', sourceAlphaFormat: 'opaque' }]);
    renderer.application.ticker.stop();
    return renderer.withExportSession(async (session) => [.02, .04].map((time) => {
      const frame = session.captureCanvas({ time, bounds: { x: 0, y: 0, width: 128, height: 52 } });
      const output = document.createElement('canvas'); output.width = 128; output.height = 52;
      const context = output.getContext('2d')!; context.drawImage(frame, 0, 0);
      return [...context.getImageData(64, 26, 1, 1).data];
    }));
  });
  expect(pixels).toEqual([[0, 255, 0, 255], [0, 0, 255, 255]]);
});
