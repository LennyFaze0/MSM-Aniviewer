import { MANIPULATION_MODES, modeLabel, type ManipulationMode } from '../core/manipulation-modes';
import './tool-mode-picker.css';

const descriptions = { select: 'Choose sprites', move: 'Change position', rotate: 'Turn sprites', scale: 'Resize sprites', pan: 'Move the camera' };

export class ToolModePicker {
  private readonly dialog = document.createElement('dialog');
  private readonly buttons: HTMLButtonElement[];
  private animation?: Animation;
  private mode: ManipulationMode = 'select';
  private closing = false;
  private backdropDown = false;
  private pressedTile?: HTMLButtonElement;

  constructor(
    private readonly trigger: HTMLButtonElement,
    icon: (name: string) => string,
    private readonly onOpen: () => void,
    private readonly onSelect: (mode: ManipulationMode) => void,
  ) {
    this.dialog.id = 'toolModePicker'; this.dialog.className = 'tool-mode-picker';
    this.dialog.setAttribute('aria-labelledby', 'toolModePickerTitle');
    this.dialog.setAttribute('aria-describedby', 'toolModePickerHint');
    this.dialog.innerHTML = `<header><h2 id="toolModePickerTitle">Sprite tools</h2>
      <span id="toolModePickerHint">Choose a mode to return to editing.</span></header>
      <div class="tool-mode-grid" role="group" aria-label="Manipulation modes">
        ${MANIPULATION_MODES.map((mode) => `<button type="button" class="tool-mode-tile" data-picker-mode="${mode}" aria-pressed="false">
          ${icon(mode)}<strong>${modeLabel(mode)}</strong><span>${descriptions[mode]}</span></button>`).join('')}
        <button type="button" class="tool-mode-tile tool-mode-cancel" data-picker-cancel>
          ${icon('close')}<strong>Cancel</strong><span>Keep current mode</span></button>
      </div>`;
    this.buttons = [...this.dialog.querySelectorAll<HTMLButtonElement>('button')];
    document.body.append(this.dialog);
    trigger.setAttribute('aria-haspopup', 'dialog'); trigger.setAttribute('aria-controls', this.dialog.id);
    trigger.setAttribute('aria-expanded', 'false');
    this.dialog.addEventListener('click', (event) => {
      if (this.closing) return;
      const button = (event.target as Element).closest<HTMLButtonElement>('button');
      // Opening on touch release can retarget its subsequent click into the dialog.
      // Only accept a pointer click that started on this tile, or keyboard activation.
      if (event.detail > 0 && button !== this.pressedTile) {
        event.preventDefault();
        this.buttons.find((tile) => tile.dataset.pickerMode === this.mode)!.focus({ preventScroll: true });
        return;
      }
      this.pressedTile = undefined;
      if (button?.dataset.pickerMode) {
        this.onSelect(button.dataset.pickerMode as ManipulationMode);
        this.close();
      } else if (button?.hasAttribute('data-picker-cancel')) this.close();
    });
    // Dismiss only an outside press/release pair, not the opener's compatibility click.
    this.dialog.addEventListener('pointerdown', (event) => {
      this.pressedTile = (event.target as Element).closest<HTMLButtonElement>('button') ?? undefined;
      this.backdropDown = event.target === this.dialog && this.outside(event);
    });
    this.dialog.addEventListener('pointerup', (event) => {
      const dismiss = this.backdropDown && event.target === this.dialog && this.outside(event);
      this.backdropDown = false;
      if (dismiss) this.close();
    });
    this.dialog.addEventListener('pointercancel', () => { this.backdropDown = false; this.pressedTile = undefined; });
    this.dialog.addEventListener('cancel', (event) => { event.preventDefault(); this.close(); });
    this.dialog.addEventListener('keydown', (event) => {
      // Do not let viewer shortcuts edit sprites while keyboard focus is in the popout.
      event.stopPropagation();
      if (this.closing) { event.preventDefault(); return; }
      if (event.key === 'Escape') { event.preventDefault(); this.close(); return; }
      const index = this.buttons.indexOf(document.activeElement as HTMLButtonElement);
      if (event.key === 'Tab') {
        event.preventDefault();
        this.buttons[(Math.max(0, index) + (event.shiftKey ? -1 : 1) + this.buttons.length) % this.buttons.length]!.focus({ preventScroll: true });
        return;
      }
      const delta = ({ ArrowLeft: -1, ArrowRight: 1, ArrowUp: -3, ArrowDown: 3 } as Record<string, number>)[event.key];
      if (delta !== undefined || event.key === 'Home' || event.key === 'End') {
        event.preventDefault();
        const next = event.key === 'Home' ? 0 : event.key === 'End' ? this.buttons.length - 1
          : (Math.max(0, index) + delta! + this.buttons.length) % this.buttons.length;
        this.buttons[next]!.focus({ preventScroll: true });
      }
    });
    window.addEventListener('resize', () => this.close(false));
    window.visualViewport?.addEventListener('resize', () => this.close(false));
  }

  get isOpen(): boolean { return this.dialog.open; }

  setMode(mode: ManipulationMode, available: boolean): void {
    this.mode = mode; this.trigger.disabled = !available;
    this.trigger.setAttribute('aria-label', `Choose tool (current: ${modeLabel(mode)})`);
    this.trigger.title = `Tools: ${modeLabel(mode)}`;
    this.trigger.querySelector('[data-current-tool]')!.textContent = modeLabel(mode);
    for (const button of this.buttons) if (button.dataset.pickerMode) {
      button.setAttribute('aria-pressed', String(button.dataset.pickerMode === mode));
    }
    if (!available) this.close(false);
  }

  toggle(): void {
    if (this.trigger.disabled) return;
    if (this.isOpen) { this.close(); return; }
    this.onOpen();
    this.closing = false; this.backdropDown = false; this.pressedTile = undefined;
    this.dialog.dataset.state = 'open';
    this.dialog.showModal();
    this.position();
    this.trigger.setAttribute('aria-expanded', 'true');
    this.buttons.find((button) => button.dataset.pickerMode === this.mode)!.focus({ preventScroll: true });
    this.animate(false);
  }

  close(animated = true): void {
    if (!this.isOpen || (this.closing && animated)) return;
    this.animation?.cancel(); this.animation = undefined;
    this.closing = true; this.backdropDown = false; this.pressedTile = undefined;
    this.dialog.dataset.state = 'closing';
    this.trigger.setAttribute('aria-expanded', 'false');
    if (animated && !matchMedia('(prefers-reduced-motion: reduce)').matches) this.animate(true);
    else this.finishClose();
  }

  private finishClose(): void {
    this.animation?.cancel(); this.animation = undefined;
    this.dialog.close(); this.closing = false;
    this.dialog.dataset.state = 'closed';
    if (!this.trigger.disabled && this.trigger.getClientRects().length) this.trigger.focus({ preventScroll: true });
  }

  private animate(closing: boolean): void {
    if (matchMedia('(prefers-reduced-motion: reduce)').matches) return;
    this.animation?.cancel();
    const panel = this.dialog.getBoundingClientRect(), button = this.trigger.getBoundingClientRect();
    const x = button.x + button.width / 2 - (panel.x + panel.width / 2);
    const y = button.y + button.height / 2 - (panel.y + panel.height / 2);
    const small = { transform: `translate(${x}px, ${y}px) scale(.12)`, opacity: 0 };
    const full = { transform: 'translate(0, 0) scale(1)', opacity: 1 };
    const animation = this.dialog.animate(closing ? [full, small] : [small, full], {
      duration: closing ? 160 : 220, easing: closing ? 'ease-in' : 'cubic-bezier(.2,.8,.2,1)',
    });
    this.animation = animation;
    animation.onfinish = () => {
      if (this.animation !== animation) return;
      this.animation = undefined;
      if (closing) this.finishClose();
    };
  }

  private outside(event: PointerEvent): boolean {
    const b = this.dialog.getBoundingClientRect();
    return event.clientX < b.left || event.clientX > b.right || event.clientY < b.top || event.clientY > b.bottom;
  }

  private position(): void {
    const view = window.visualViewport, x = view?.offsetLeft ?? 0, y = view?.offsetTop ?? 0;
    const width = view?.width ?? window.innerWidth, height = view?.height ?? window.innerHeight;
    const scale = Number(getComputedStyle(document.documentElement).getPropertyValue('--ui-scale')) || 1;
    this.dialog.style.width = `${Math.max(0, Math.min(380 * scale, width - 16))}px`;
    this.dialog.style.maxHeight = `${Math.max(0, height - 16)}px`;
    const box = this.dialog.getBoundingClientRect(), button = this.trigger.getBoundingClientRect();
    const left = Math.max(x + 8, Math.min(button.x + button.width / 2 - box.width / 2, x + width - box.width - 8));
    const top = Math.max(y + 8, Math.min(button.top - box.height - 10, y + height - box.height - 8));
    this.dialog.style.left = `${left}px`; this.dialog.style.top = `${top}px`;
  }
}
