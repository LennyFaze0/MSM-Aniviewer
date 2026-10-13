import type { CostumeDefinition, CostumeRemap } from './costume';
import type { AnimationLayer, AnimationTrack } from './types';

type Clone = CostumeDefinition['clone_layers'][number];
const key = (name: string) => name.trim().toLowerCase();
const normalized = (name: string) => key(name).replace(/\s+/g, ' ').replace(/\s*\(\d+\)$/, '').trim();
const isShade = (name: string) => / shade|shadow| mask/i.test(name);

export function findCostumeLayer(layers: AnimationLayer[], name: string): AnimationLayer | undefined {
  return layers.find((layer) => key(layer.name) === key(name))
    ?? layers.find((layer) => key(layer.name) === normalized(name));
}

export interface CostumeCloneResult {
  remaps: Map<number, CostumeRemap>;
  opaque: Set<number>;
  overlay: Set<number>;
}

/** Work on an already-cloned track; costume metadata and the base document stay immutable. */
export function applyCostumeClones(track: AnimationTrack, costume: CostumeDefinition): CostumeCloneResult {
  const result: CostumeCloneResult = { remaps: new Map(), opaque: new Set(), overlay: new Set() };
  const remaps = new Map((costume.remaps ?? []).map((entry) => [key(entry.display_name), entry]));
  const findRemap = (...names: string[]) => names.map((name) => remaps.get(key(name)) ?? remaps.get(normalized(name))).find(Boolean);
  const canonical = new Set<string>();
  const pairs: Array<{ source: AnimationLayer; copy: AnimationLayer }> = [];
  const dependencies = new Map<number, AnimationLayer[]>();
  let nextId = Math.max(-1, ...track.layers.map((layer) => layer.id)) + 1;

  const apply = (clone: Clone, seed: boolean) => {
    let name = clone.new_layer || clone.name, sourceName = clone.source_layer || clone.resource;
    const referenceName = clone.reference_layer || clone.sheet;
    if (!name || !sourceName) return;
    const exact = (label: string) => track.layers.find((layer) => key(layer.name) === key(label));
    if ((!exact(sourceName) && exact(name)) || (!seed && canonical.has(key(sourceName)) && !canonical.has(key(name)))) {
      [name, sourceName] = [sourceName, name];
    }
    const existing = exact(name);
    const source = findCostumeLayer(track.layers, sourceName)
      ?? findCostumeLayer(track.layers, name) ?? findCostumeLayer(track.layers, referenceName);
    const reference = findCostumeLayer(track.layers, referenceName);
    if (!source || !reference || key(name) === key(sourceName)) return;
    const alternate = /(?: shade| shadow| mask)$/i.test(reference.name)
      ? findCostumeLayer(track.layers, reference.name.replace(/(?: shade| shadow| mask)$/i, '')) : undefined;
    const anchor = alternate ?? reference;
    const reuse = existing && (seed || canonical.has(key(name)));
    const copy = reuse ? existing : { ...structuredClone(source), name, id: nextId++ };
    if (copy === anchor) return;
    if (reuse) track.layers.splice(track.layers.indexOf(copy), 1);
    const mode = clone.insert_mode ?? (clone.variant_index | 0);
    track.layers.splice(track.layers.indexOf(anchor) + (mode < 0 ? 1 : 0), 0, copy);
    if (seed) { canonical.add(key(name)); copy.visible = false; return; }
    const remap = findRemap(name, sourceName, referenceName);
    if (remap) result.remaps.set(copy.id, remap);
    if (alternate) {
      dependencies.set(copy.id, [alternate, reference]);
      result.overlay.add(copy.id);
    }
    const opacity = source.frames.find((frame) => frame.opacity && frame.opacity.immediate !== -1)?.opacity?.value
      ?? source.frames[0]?.opacity?.value ?? 100;
    if ((isShade(source.name) && !isShade(name)) || (remap?.sheet.toLowerCase().includes('costume') && opacity < 99.5)) {
      result.opaque.add(copy.id);
    }
    pairs.push({ source, copy });
  };
  for (const clone of track.clone_layers ?? []) apply(clone, true);
  for (const clone of costume.clone_layers ?? []) apply(clone, false);
  for (const layer of track.layers) {
    // Name fallbacks are for resolving clone definitions, not activating unused aliases.
    const remap = result.remaps.get(layer.id) ?? remaps.get(key(layer.name));
    if (remap) result.remaps.set(layer.id, remap);
    if (canonical.has(key(layer.name))) layer.visible = Boolean(remap && key(remap.resource) !== 'empty');
  }
  const replacements = pairs.filter(({ source, copy }) => (
    key(result.remaps.get(source.id)?.resource ?? '') === 'empty'
      && Boolean(result.remaps.get(copy.id)?.resource)
      && key(result.remaps.get(copy.id)!.resource) !== 'empty'
  ));
  for (const { source, copy } of replacements) {
    // Multiple replacements are an authored overlay stack, not duplicate artwork.
    if (replacements.filter((pair) => pair.source.id === source.id).length !== 1) continue;
    result.remaps.set(source.id, result.remaps.get(copy.id)!);
    result.remaps.set(copy.id, { display_name: copy.name, resource: 'empty', sheet: 'empty.xml', frame_mappings: [] });
    source.visible = true;
    copy.visible = false;
  }
  for (const [id, refs] of dependencies) {
    const current = track.layers.findIndex((layer) => layer.id === id);
    const before = Math.min(...refs.map((layer) => track.layers.indexOf(layer)));
    if (current > before) track.layers.splice(before, 0, track.layers.splice(current, 1)[0]!);
  }
  return result;
}
