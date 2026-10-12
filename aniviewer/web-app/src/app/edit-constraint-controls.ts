import { ALL_EDIT_CHANNELS, type EditConstraint } from '../core/edit-constraints';
import type { ViewerSession } from './viewer-session';
import './edit-constraint-controls.css';

export class EditConstraintControls {
  private readonly root = document.createElement('fieldset');
  private readonly driver: HTMLSelectElement;
  private readonly mode: HTMLSelectElement;
  private busy = false;

  constructor(host: HTMLElement, private readonly session: ViewerSession, private readonly cancelGesture: () => void) {
    this.root.className = 'control-group edit-links';
    this.root.id = 'editLinks';
    this.root.innerHTML = `
      <legend>Edit Links</legend>
      <p class="control-hint">Link your edits, not the original animation. Choose a driver, choose what follows it, then apply.</p>
      <label class="link-field">Driver segment<select id="editLinkDriver" aria-label="Link driver"></select></label>
      <button type="button" class="button" data-link="selected-driver">Use selected as driver</button>
      <label class="link-field">Behavior<select id="editLinkMode" aria-label="Link behavior">
        <option value="off">Off - edit independently</option>
        <option value="rigid">Move together - all transforms</option>
        <option value="selective">Choose what follows</option>
      </select></label>
      <div id="editLinkChannels" class="link-channels" hidden>
        <label><input type="checkbox" data-link-channel="movement" checked> Follow movement</label>
        <label><input type="checkbox" data-link-channel="rotation" checked> Follow rotation</label>
        <label><input type="checkbox" data-link-channel="scale" checked> Follow scale</label>
      </div>
      <div class="control-command-grid">
        <button type="button" class="button" data-link="descendants">Use animation descendants</button>
        <button type="button" class="button" data-link="selected-members">Use selected segments</button>
      </div>
      <p class="control-hint">Following segments (including hidden ones):</p>
      <div id="editLinkMembers" class="link-members" role="group" aria-label="Following segments"></div>
      <div class="control-command-grid">
        <button type="button" class="button" data-link="apply">Apply links</button>
        <button type="button" class="button" data-link="remove">Remove rule</button>
      </div>
      <p id="editLinkStatus" class="control-hint" role="status"></p>
      <p id="editLinkError" class="control-hint" role="alert"></p>
      <p class="control-hint">Changing a link keeps the current pose and affects your next edits only. Off stops future following; Undo reverses an earlier edit. In Move together mode, selecting a member edits its driver.</p>
      <details><summary>Rotation, scale and existing keyframes</summary>
        <p class="control-hint">Rotation and scale move followers around the driver's anchor. Follow movement only controls dragging. Group X/Y scale uses world axes. With Choose what follows, members can also be edited independently.</p>
        <p class="control-hint">Existing keyframes, sprite swaps and animation timing are untouched. Links apply across the animation; they are not new keyframes. Links and edits are cleared when changing animation or files.</p>
      </details>
      <div id="editLinkRules" class="link-rules" aria-label="Saved edit links"></div>`;
    host.append(this.root);
    this.driver = this.root.querySelector('#editLinkDriver')!;
    this.mode = this.root.querySelector('#editLinkMode')!;
    this.driver.addEventListener('change', () => {
      this.cancelGesture();
      session.selectLayer(Number(this.driver.value));
      this.loadDriver();
    });
    this.mode.addEventListener('change', () => this.updateChannels());
    this.root.addEventListener('change', (event) => {
      if (event.target !== this.driver) this.status('Not applied yet. Choose Apply links to use these settings.');
    });
    this.root.addEventListener('click', (event) => {
      const button = (event.target as Element).closest<HTMLButtonElement>('button');
      if (!button || this.busy) return;
      const id = Number(this.driver.value);
      if (button.dataset.linkDriver) {
        this.cancelGesture(); this.driver.value = button.dataset.linkDriver;
        session.selectLayer(Number(this.driver.value)); this.loadDriver(); return;
      }
      switch (button.dataset.link) {
        case 'selected-driver': {
          const selected = session.snapshot().selectedLayerIds;
          if (selected.length !== 1) { this.error('Select one segment to use as the driver.'); return; }
          this.driver.value = String(selected[0]); this.loadDriver(); break;
        }
        case 'descendants': this.chooseMembers(this.descendants(id)); break;
        case 'selected-members': this.chooseMembers(session.snapshot().selectedLayerIds.filter((member) => member !== id)); break;
        case 'apply': this.apply(); break;
        case 'remove': this.cancelGesture(); session.removeConstraint(id); this.loadDriver(); break;
      }
    });
    session.subscribe((_snapshot, change) => {
      if (change === 'document' || change === 'track') this.refreshTrack();
      else if (change === 'constraints' || change === 'history') this.loadDriver();
    });
    this.refreshTrack();
  }

  setBusy(busy: boolean): void { this.busy = busy; this.root.disabled = busy || !this.session.track; }
  private status(message: string): void { this.root.querySelector('#editLinkStatus')!.textContent = message; }
  private error(message = ''): void { this.root.querySelector('#editLinkError')!.textContent = message; }

  private refreshTrack(): void {
    this.driver.replaceChildren(...(this.session.track?.layers ?? []).map((layer) => new Option(`${layer.name} (#${layer.id})`, String(layer.id))));
    this.setBusy(this.busy);
    this.loadDriver();
  }

  private descendants(id: number): number[] {
    const result = new Set<number>(), queue = [id];
    while (queue.length) {
      const parent = queue.shift();
      for (const layer of this.session.track?.layers ?? []) {
        if (layer.parent !== parent || layer.id === id || result.has(layer.id)) continue;
        result.add(layer.id); queue.push(layer.id);
      }
    }
    return [...result];
  }

  private chooseMembers(ids: readonly number[]): void {
    this.root.querySelectorAll<HTMLInputElement>('[data-link-member]').forEach((input) => { input.checked = ids.includes(Number(input.value)); });
    this.status(`${ids.length} chosen. Choose Apply links to use these settings.`);
    this.error();
  }

  private updateChannels(): void {
    this.root.querySelector<HTMLElement>('#editLinkChannels')!.hidden = this.mode.value !== 'selective';
  }

  private loadDriver(): void {
    const rules = this.session.snapshot().constraints;
    const id = Number(this.driver.value), rule = rules.find((entry) => entry.driverId === id);
    this.mode.value = rule?.enabled ? rule.mode : 'off';
    this.root.querySelectorAll<HTMLInputElement>('[data-link-channel]').forEach((input) => {
      input.checked = (rule?.channels ?? ALL_EDIT_CHANNELS)[input.dataset.linkChannel as keyof typeof ALL_EDIT_CHANNELS];
    });
    const members = this.root.querySelector('#editLinkMembers')!;
    members.replaceChildren();
    const chosen = rule?.memberIds ?? this.descendants(id);
    for (const layer of this.session.track?.layers ?? []) {
      if (layer.id === id) continue;
      const label = document.createElement('label'), input = document.createElement('input');
      input.type = 'checkbox'; input.value = String(layer.id); input.dataset.linkMember = String(layer.id); input.checked = chosen.includes(layer.id);
      const owner = rules.find((entry) => entry.driverId !== id && entry.memberIds.includes(layer.id));
      const text = document.createElement('span');
      text.textContent = `${layer.name} (#${layer.id})${owner ? ` - already linked to #${owner.driverId}` : ''}`;
      label.append(input, text); members.append(label);
    }
    const list = this.root.querySelector('#editLinkRules')!;
    list.replaceChildren();
    for (const entry of rules) {
      const button = document.createElement('button');
      button.type = 'button'; button.className = 'button'; button.dataset.linkDriver = String(entry.driverId);
      const name = this.session.track?.layers.find((layer) => layer.id === entry.driverId)?.name;
      button.textContent = `${name}: ${entry.enabled ? entry.mode === 'rigid' ? 'Move together' : 'Choose what follows' : 'Off'} (${entry.memberIds.length})`;
      list.append(button);
    }
    this.root.querySelector<HTMLButtonElement>('[data-link="remove"]')!.disabled = !rule;
    this.updateChannels(); this.error();
    this.status(rule?.enabled ? 'Applied. Your next edits will follow these links. Original animation stays active.' : 'Off. No new edits from this driver are inherited. Existing edits are kept.');
  }

  private apply(): void {
    const id = Number(this.driver.value);
    const memberIds = [...this.root.querySelectorAll<HTMLInputElement>('[data-link-member]:checked')].map((input) => Number(input.value));
    try {
      this.cancelGesture();
      if (this.mode.value === 'off' && !memberIds.length) { this.session.removeConstraint(id); this.loadDriver(); return; }
      const previous = this.session.snapshot().constraints.find((rule) => rule.driverId === id);
      const rule: EditConstraint = {
        driverId: id, memberIds, enabled: this.mode.value !== 'off',
        mode: this.mode.value === 'off' ? previous?.mode ?? 'rigid' : this.mode.value as EditConstraint['mode'],
        channels: {
          movement: this.root.querySelector<HTMLInputElement>('[data-link-channel="movement"]')!.checked,
          rotation: this.root.querySelector<HTMLInputElement>('[data-link-channel="rotation"]')!.checked,
          scale: this.root.querySelector<HTMLInputElement>('[data-link-channel="scale"]')!.checked,
        },
      };
      this.session.setConstraint(rule); this.loadDriver();
    } catch (error) { this.error(error instanceof Error ? error.message : String(error)); }
  }
}
