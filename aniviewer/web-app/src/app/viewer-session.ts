import { animationDuration } from '../core/animation';
import type { AnimationDocument, AnimationTrack } from '../core/types';
import { CommandHistory, type CommandHistoryState, type EditorCommand } from './command-history';
import { DEFAULT_LAYER_EDIT, normalizeLayerEdit, type LayerTransformEdit } from '../core/layer-transform';
import { cloneConstraints, cloneOperations, editDriver, planTransformOperations, validateConstraints, type EditConstraint, type TransformOperation } from '../core/edit-constraints';
import { cloneIKRigs, validateIKRigs, type IKRig } from '../core/ik-constraints';
export type { LayerTransformEdit } from '../core/layer-transform';

export interface ViewerSessionSnapshot {
  document?: AnimationDocument;
  trackIndex: number;
  currentTime: number;
  playing: boolean;
  loop: boolean;
  selectedLayerIds: readonly number[];
  hiddenLayerIds: readonly number[];
  layerEdits: ReadonlyMap<number, LayerTransformEdit>;
  transformOperations: readonly TransformOperation[];
  constraints: readonly EditConstraint[];
  ikRigs: readonly IKRig[];
  history: CommandHistoryState;
}

export type ViewerSessionChange =
  | 'document'
  | 'track'
  | 'time'
  | 'playback'
  | 'loop'
  | 'selection'
  | 'visibility'
  | 'constraints'
  | 'ik'
  | 'edit'
  | 'history';

export type ViewerSessionListener = (
  snapshot: ViewerSessionSnapshot,
  change: ViewerSessionChange,
) => void;

function sameLayerEdit(left: LayerTransformEdit, right: LayerTransformEdit): boolean {
  return (
    left.offsetX === right.offsetX
    && left.offsetY === right.offsetY
    && left.rotation === right.rotation
    && left.scaleX === right.scaleX
    && left.scaleY === right.scaleY
  );
}

export class ViewerSession {
  private activeDocument?: AnimationDocument;
  private activeTrackIndex = 0;
  private time = 0;
  private isPlaying = false;
  private isLooping = true;
  private readonly selectedLayers = new Set<number>();
  private readonly hiddenLayers = new Set<number>();
  private readonly edits = new Map<number, LayerTransformEdit>();
  private readonly listeners = new Set<ViewerSessionListener>();
  private readonly commands = new CommandHistory<ViewerSession>();
  private transformBefore?: Map<number, LayerTransformEdit>;
  private operations: TransformOperation[] = [];
  private rules: EditConstraint[] = [];
  private operationsBefore?: TransformOperation[];
  private ikRigs: IKRig[] = [];
  private ikBefore?: { id: string; rigs: IKRig[] };

  get transforming(): boolean { return Boolean(this.transformBefore || this.ikBefore); }

  get document(): AnimationDocument | undefined {
    return this.activeDocument;
  }

  get trackIndex(): number {
    return this.activeTrackIndex;
  }

  get track(): AnimationTrack | undefined {
    return this.activeDocument?.anims[this.activeTrackIndex];
  }

  get currentTime(): number {
    return this.time;
  }

  get playing(): boolean {
    return this.isPlaying;
  }

  get loop(): boolean {
    return this.isLooping;
  }

  get history(): CommandHistoryState {
    return this.commands.state;
  }

  snapshot(): ViewerSessionSnapshot {
    return {
      document: this.activeDocument,
      trackIndex: this.activeTrackIndex,
      currentTime: this.time,
      playing: this.isPlaying,
      loop: this.isLooping,
      selectedLayerIds: [...this.selectedLayers],
      hiddenLayerIds: [...this.hiddenLayers],
      layerEdits: new Map([...this.edits].map(([id, edit]) => [id, { ...edit }])),
      transformOperations: cloneOperations(this.operations),
      constraints: cloneConstraints(this.rules),
      ikRigs: cloneIKRigs(this.ikRigs),
      history: this.commands.state,
    };
  }

  subscribe(listener: ViewerSessionListener): () => void {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  }

  setDocument(document: AnimationDocument | undefined): void {
    this.activeDocument = document;
    this.activeTrackIndex = 0;
    this.resetTransientState();
    this.commands.clear();
    this.emit('document');
  }

  setTrack(index: number): boolean {
    if (!this.activeDocument?.anims[index]) return false;
    this.activeTrackIndex = index;
    this.resetTransientState();
    this.commands.clear();
    this.emit('track');
    return true;
  }

  setTime(time: number): void {
    const duration = this.track ? animationDuration(this.track) : 0;
    const next = Math.max(0, Math.min(duration, Number.isFinite(time) ? time : 0));
    if (next === this.time) return;
    this.time = next;
    this.emit('time');
  }

  setPlaying(playing: boolean): void {
    if (playing === this.isPlaying) return;
    this.isPlaying = playing;
    this.emit('playback');
  }

  setLoop(loop: boolean): void {
    if (loop === this.isLooping) return;
    this.isLooping = loop;
    this.emit('loop');
  }

  toggleLoop(): boolean {
    this.setLoop(!this.isLooping);
    return this.isLooping;
  }

  selectLayer(layerId: number, additive = false): void {
    if (!this.track?.layers.some((layer) => layer.id === layerId)) return;
    if (!additive) {
      if (this.selectedLayers.size === 1 && this.selectedLayers.has(layerId)) return;
      this.selectedLayers.clear();
      this.selectedLayers.add(layerId);
    } else if (this.selectedLayers.has(layerId)) {
      this.selectedLayers.delete(layerId);
    } else {
      this.selectedLayers.add(layerId);
    }
    this.emit('selection');
  }

  clearSelection(): void {
    if (!this.selectedLayers.size) return;
    this.selectedLayers.clear();
    this.emit('selection');
  }

  isLayerSelected(layerId: number): boolean {
    return this.selectedLayers.has(layerId);
  }

  setLayerVisible(layerId: number, visible: boolean): void {
    if (visible) this.hiddenLayers.delete(layerId);
    else this.hiddenLayers.add(layerId);
    this.emit('visibility');
  }

  toggleLayerVisible(layerId: number): boolean {
    const visible = this.hiddenLayers.has(layerId);
    this.setLayerVisible(layerId, visible);
    return visible;
  }

  isLayerVisible(layerId: number): boolean {
    return !this.hiddenLayers.has(layerId);
  }

  layerEdit(layerId: number): LayerTransformEdit {
    return { ...(this.edits.get(layerId) ?? DEFAULT_LAYER_EDIT) };
  }

  transformLayerIds(ids: readonly number[]): number[] {
    return [...new Set(ids.map((id) => editDriver(id, this.rules)))];
  }

  setConstraint(rule: EditConstraint): void {
    this.setConstraints([...this.rules.filter((entry) => entry.driverId !== rule.driverId), rule]);
  }

  removeConstraint(driverId: number): void {
    this.setConstraints(this.rules.filter((entry) => entry.driverId !== driverId));
  }

  setIKRig(rig: IKRig, label = 'Configure IK'): void {
    this.cancelTransform();
    this.replaceIKRigs([...this.ikRigs.filter((entry) => entry.id !== rig.id), rig], label);
  }

  removeIKRig(id: string): void {
    this.cancelTransform();
    this.replaceIKRigs(this.ikRigs.filter((entry) => entry.id !== id), 'Remove IK');
  }

  private replaceIKRigs(rigs: readonly IKRig[], label: string): void {
    if (!this.track) return;
    validateIKRigs(this.track, rigs);
    const before = cloneIKRigs(this.ikRigs), after = cloneIKRigs(rigs);
    if (JSON.stringify(before) === JSON.stringify(after)) return;
    this.commands.execute(this, { label, execute: () => { this.ikRigs = cloneIKRigs(after); }, undo: () => { this.ikRigs = cloneIKRigs(before); } });
    this.emit('ik');
  }

  beginIKTarget(id: string): boolean {
    this.cancelTransform();
    if (!this.ikRigs.some((rig) => rig.id === id && rig.enabled)) return false;
    this.ikBefore = { id, rigs: cloneIKRigs(this.ikRigs) };
    return true;
  }

  previewIKTarget(x: number, y: number): void {
    if (!this.ikBefore || ![x, y].every(Number.isFinite)) return;
    const { id, rigs } = this.ikBefore;
    this.ikRigs = cloneIKRigs(rigs).map((rig) => rig.id === id ? { ...rig, target: { ...rig.target, x, y } } : rig);
    this.emit('ik');
  }

  commitIKTarget(): boolean {
    if (!this.ikBefore) return false;
    const before = this.ikBefore.rigs, after = cloneIKRigs(this.ikRigs);
    this.ikBefore = undefined;
    const changed = JSON.stringify(before) !== JSON.stringify(after);
    if (changed) this.commands.execute(this, {
      label: 'Move IK target', execute: () => { this.ikRigs = cloneIKRigs(after); }, undo: () => { this.ikRigs = cloneIKRigs(before); },
    });
    this.emit('ik');
    return changed;
  }

  private setConstraints(rules: readonly EditConstraint[]): void {
    if (!this.track) return;
    validateConstraints(this.track, rules);
    this.cancelTransform();
    const before = cloneConstraints(this.rules), after = cloneConstraints(rules);
    if (JSON.stringify(before) === JSON.stringify(after)) return;
    this.commands.execute(this, {
      label: 'Change edit links',
      execute: () => { this.rules = cloneConstraints(after); },
      undo: () => { this.rules = cloneConstraints(before); },
    });
    this.emit('constraints');
  }

  resetLayerEdits(ids: readonly number[]): boolean {
    this.cancelTransform();
    const drivers = new Set(this.transformLayerIds(ids));
    const before = this.operations;
    const after = before.filter((op) => !drivers.has(op.driverId));
    if (before.length === after.length) return false;
    const beforeEdits = new Map([...drivers].map((id) => [id, this.layerEdit(id)]));
    this.commands.execute(this, {
      label: 'Reset selected edits',
      execute: () => { this.operations = after; drivers.forEach((id) => this.replaceEdit(id, DEFAULT_LAYER_EDIT)); },
      undo: () => { this.operations = before; beforeEdits.forEach((edit, id) => this.replaceEdit(id, edit)); },
    });
    this.emit('edit');
    return true;
  }

  applyLayerEdit(layerId: number, edit: Partial<LayerTransformEdit>, label = 'Transform layer'): boolean {
    return this.applyLayerEdits(new Map([[layerId, edit]]), label);
  }

  applyLayerEdits(edits: ReadonlyMap<number, Partial<LayerTransformEdit>>, label = 'Transform layers'): boolean {
    this.beginTransform([...edits.keys()]);
    this.previewTransform(edits);
    return this.commitTransform(label);
  }

  beginTransform(ids: readonly number[]): void {
    this.cancelTransform();
    const valid = new Set(this.track?.layers.map((layer) => layer.id));
    const entries = this.transformLayerIds(ids.filter((id) => valid.has(id))).map((id) => [id, this.layerEdit(id)] as const);
    if (entries.length) {
      this.transformBefore = new Map(entries);
      this.operationsBefore = this.operations;
    }
  }

  previewTransform(edits: ReadonlyMap<number, Partial<LayerTransformEdit>>): void {
    if (!this.transformBefore) return;
    const requested = new Map(this.transformBefore);
    for (const [source, edit] of edits) {
      const id = editDriver(source, this.rules), before = this.transformBefore.get(id);
      if (before) requested.set(id, normalizeLayerEdit({ ...before, ...edit }));
    }
    const operations = planTransformOperations(this.transformBefore, requested, this.rules);
    this.operations = [...this.operationsBefore!, ...operations];
    this.transformBefore.forEach((edit, id) => this.replaceEdit(id, edit));
    // A parent+member multi-selection claims each channel once. Readouts reflect only
    // independently authored inputs, never inherited deltas or lossy matrix decomposition.
    for (const op of operations) {
      const before = this.transformBefore.get(op.driverId)!;
      this.replaceEdit(op.driverId, normalizeLayerEdit({
        offsetX: before.offsetX + op.delta.offsetX, offsetY: before.offsetY + op.delta.offsetY,
        rotation: before.rotation + op.delta.rotation, scaleX: before.scaleX * op.delta.scaleX, scaleY: before.scaleY * op.delta.scaleY,
      }));
    }
    this.emit('edit');
  }

  commitTransform(label = 'Transform layers'): boolean {
    const before = this.transformBefore;
    const operationsBefore = this.operationsBefore, operationsAfter = this.operations;
    this.transformBefore = undefined;
    this.operationsBefore = undefined;
    if (!before) return false;
    const after = new Map([...before.keys()].map((id) => [id, this.layerEdit(id)]));
    const changed = [...before].some(([id, edit]) => !sameLayerEdit(edit, after.get(id)!));
    if (changed) this.commands.execute(this, {
      label,
      execute: () => { this.operations = operationsAfter; after.forEach((edit, id) => this.replaceEdit(id, edit)); },
      undo: () => { this.operations = operationsBefore!; before.forEach((edit, id) => this.replaceEdit(id, edit)); },
    });
    this.emit('edit');
    return changed;
  }

  cancelTransform(): void {
    if (this.ikBefore) {
      this.ikRigs = this.ikBefore.rigs;
      this.ikBefore = undefined;
      this.emit('ik');
    }
    if (!this.transformBefore) return;
    this.transformBefore.forEach((edit, id) => this.replaceEdit(id, edit));
    this.operations = this.operationsBefore!;
    this.operationsBefore = undefined;
    this.transformBefore = undefined;
    this.emit('edit');
  }

  private replaceEdit(id: number, edit: LayerTransformEdit): void {
    if (sameLayerEdit(edit, DEFAULT_LAYER_EDIT)) this.edits.delete(id);
    else this.edits.set(id, { ...edit });
  }

  execute(command: EditorCommand<ViewerSession>): void {
    this.commands.execute(this, command);
    this.emit('history');
  }

  undo(): boolean {
    this.cancelTransform();
    const changed = this.commands.undo(this);
    if (changed) this.emit('history');
    return changed;
  }

  redo(): boolean {
    this.cancelTransform();
    const changed = this.commands.redo(this);
    if (changed) this.emit('history');
    return changed;
  }

  private resetTransientState(): void {
    this.transformBefore = undefined;
    this.operationsBefore = undefined;
    this.operations = [];
    this.rules = [];
    this.ikBefore = undefined;
    this.ikRigs = [];
    this.time = 0;
    this.isPlaying = false;
    this.selectedLayers.clear();
    this.hiddenLayers.clear();
    for (const layer of this.track?.layers ?? []) {
      if (layer.visible === false) this.hiddenLayers.add(layer.id);
    }
    this.edits.clear();
  }

  private emit(change: ViewerSessionChange): void {
    const snapshot = this.snapshot();
    this.listeners.forEach((listener) => listener(snapshot, change));
  }
}
