import { readFileSync, readdirSync } from 'node:fs';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { execFileSync } from 'node:child_process';
import { expect, test } from 'vitest';
import { parseAnimationBin } from './bin-parser';
import { applyCostumeDefinition, parseCostumeBin } from './costume';
import type { CostumeAtlas } from './costume-appearance';

const root = process.env.ANIVIEWER_COSTUME_CORPUS;
test.skipIf(!root)('matches desktop clone ordering, replacement visibility and mixed pivots on real costumes', () => {
  const read = (name: string, folder = 'xml_bin') => {
    const directory = join(root!, folder);
    const filename = readdirSync(directory).find((entry) => entry.toLowerCase() === name.toLowerCase());
    if (!filename) throw new Error(`Missing corpus fixture: ${folder}/${name}`);
    const data = readFileSync(join(directory, filename));
    return data.buffer.slice(data.byteOffset, data.byteOffset + data.byteLength) as ArrayBuffer;
  };
  const cases = [['a', '07'], ['a', '09'], ['z11', '03'], ['den_rare', '01']].map(([monster, costumeId]) => {
    const document = parseAnimationBin(read(`monster_${monster}.bin`)).document;
    const costume = parseCostumeBin(read(`costume_${monster}_${costumeId}.bin`));
    const paths = [...new Set(costume.remaps.map((entry) => entry.sheet).filter((sheet) => sheet && !/^empty/i.test(sheet)))];
    // XML is decoded by the standard library here because Node has no DOMParser.
    const atlases = JSON.parse(execFileSync('python3', ['-c', `
import json, pathlib, sys, xml.etree.ElementTree as ET
directory = pathlib.Path(sys.argv[1]) / 'xml_resources'
files = {p.name.lower(): p for p in directory.iterdir()}
result = []
for name in json.load(sys.stdin):
    path = files[pathlib.Path(name).name.lower()]
    root = ET.parse(path).getroot()
    sprites = []
    for s in root.iter('sprite'):
        num = lambda key, fallback=0: float(s.get(key, fallback))
        sprites.append(dict(name=s.get('n'), x=num('x'), y=num('y'), width=num('w'), height=num('h'),
            originalWidth=num('oW', num('w')), originalHeight=num('oH', num('h')), offsetX=num('oX'), offsetY=num('oY'),
            pivotX=num('pX', .5), pivotY=num('pY', .5), rotated=s.get('r') in ('true', '1')))
    result.append(dict(sourceName=name, hires=root.get('hires', '').lower() in ('true', '1', 'yes'), sprites=sprites))
json.dump(result, sys.stdout)
`, root!], { input: JSON.stringify(paths), encoding: 'utf8' })) as Array<{ sourceName: string; hires: boolean; sprites: import('./types').AtlasSprite[] }>;
    return { document, costume, atlases };
  });
  const reference = JSON.parse(execFileSync('python3', [fileURLToPath(new URL('../../tests/reference/costume_reference.py', import.meta.url))], {
    input: JSON.stringify(cases), encoding: 'utf8', maxBuffer: 16 * 1024 * 1024,
  }));
  cases.forEach((item, index) => {
    const atlases: CostumeAtlas[] = item.atlases.map((atlas, i) => ({
      sourceId: i + 100, sourceName: atlas.sourceName,
      data: { imagePath: atlas.sourceName, width: 2048, height: 2048, hires: atlas.hires, sprites: new Map(atlas.sprites.map((sprite) => [sprite.name.toLowerCase(), sprite])) },
    }));
    const result = applyCostumeDefinition(item.document, item.costume, [], atlases);
    expect(result.anims.map((track) => track.layers.map((layer) => ({
      name: layer.name, parent: layer.parent, anchor: [layer.anchor_x, layer.anchor_y], visible: layer.visible !== false,
      sprites: layer.frames.map((frame) => frame.sprite?.string ?? ''), opacity: layer.frames.map((frame) => frame.opacity?.value ?? 100),
      anchors: layer.costume_sprite_anchors ?? {},
    }))), `Costume corpus case ${index}: ${item.costume.remaps[0]?.sheet}`).toEqual(reference[index]);
  });
}, 30_000);
