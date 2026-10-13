import { execFileSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { expect, test } from 'vitest';
import { costumeDrawOffset } from './costume-appearance';
import type { AnimationLayer, AtlasSprite, TextureAtlasData } from './types';

test.skipIf(!process.env.ANIVIEWER_DESKTOP_COSTUME_PARITY)('draw offsets use the actual configured desktop renderer branch', () => {
  const cases = [false, true].flatMap((hires) => [0, 10, -32].map((anchor) => ({
    hires, anchor, x: .2, y: .7, width: 40, height: 60,
  })));
  const offsets = JSON.parse(execFileSync('python3', ['-c', `
import ast, json, pathlib, sys
from types import SimpleNamespace
root = pathlib.Path(sys.argv[1])
main = ast.parse((root / 'ui/main_window.py').read_text())
settings = [n.args[0].value for n in ast.walk(main) if isinstance(n, ast.Call)
    and isinstance(n.func, ast.Attribute) and n.func.attr == 'set_costume_pivot_adjustment_enabled'
    and n.args and isinstance(n.args[0], ast.Constant)]
assert settings and all(value is False for value in settings), 'Desktop pivot configuration changed; re-audit the renderer.'
tree = ast.parse((root / 'renderer/sprite_renderer.py').read_text())
branches = [n for n in ast.walk(tree) if isinstance(n, ast.If)
    and isinstance(n.test, ast.Attribute) and n.test.attr == 'costume_pivot_adjustment_enabled'
    and any(isinstance(a, ast.Assign) and isinstance(a.targets[0], ast.Name) and a.targets[0].id == 'adjusted_anchor_x' for a in n.body)]
assert len(branches) == 1
assignments = [n for n in ast.walk(tree) if isinstance(n, ast.Assign)
    and isinstance(n.targets[0], ast.Name) and n.targets[0].id == 'sprite_anchor_override'
    and isinstance(n.value, ast.Tuple) and 'adjusted_anchor_x' in ast.dump(n.value)]
assert len(assignments) == 1
code = compile(ast.fix_missing_locations(ast.Module(body=[branches[0], assignments[0]], type_ignores=[])), 'desktop-pivot', 'exec')
result = []
for case in json.load(sys.stdin):
    scale = .5 if case['hires'] else 1
    scope = dict(self=SimpleNamespace(costume_pivot_adjustment_enabled=settings[0]),
        sprite_w=case['width']*scale, sprite_h=case['height']*scale,
        sprite=SimpleNamespace(pivot_x=case['x'], pivot_y=case['y']),
        original_anchor_x=case['anchor'], original_anchor_y=case['anchor'])
    exec(code, scope)
    result.append(scope['sprite_anchor_override'])
json.dump(result, sys.stdout)
`, fileURLToPath(new URL('../../../', import.meta.url))], { input: JSON.stringify(cases), encoding: 'utf8' }));
  expect(cases.map((entry) => costumeDrawOffset(
    { anchor_x: entry.anchor, anchor_y: entry.anchor, costume_pivot_context: true } as AnimationLayer,
    { name: 'test', pivotX: entry.x, pivotY: entry.y, originalWidth: entry.width, originalHeight: entry.height } as AtlasSprite,
    { hires: entry.hires } as TextureAtlasData,
  ))).toEqual(offsets);
});
