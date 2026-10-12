export interface ControlPanelTabsOptions {
  initialTab?: string;
  storageKey?: string;
}

export class ControlPanelTabs {
  private readonly buttons: HTMLButtonElement[];
  private readonly panels: HTMLElement[];
  private readonly tabList: HTMLElement;
  private activeTab = '';

  constructor(
    private readonly root: HTMLElement,
    options: ControlPanelTabsOptions = {},
  ) {
    this.buttons = [...root.querySelectorAll<HTMLButtonElement>('[data-control-tab]')];
    this.panels = [...root.querySelectorAll<HTMLElement>('[data-control-panel]')];
    const tabList = root.querySelector<HTMLElement>('[role="tablist"]');
    if (!tabList || !this.buttons.length || !this.panels.length) {
      throw new Error('Control panel tab structure is incomplete.');
    }
    this.tabList = tabList;

    const storedTab = options.storageKey ? this.readStoredTab(options.storageKey) : undefined;
    const initialTab = storedTab ?? options.initialTab ?? this.buttons[0]!.dataset.controlTab!;
    this.bindEvents(options.storageKey);
    this.activate(initialTab);
  }

  get currentTab(): string {
    return this.activeTab;
  }

  activate(tab: string, focus = false): boolean {
    const button = this.buttons.find((candidate) => candidate.dataset.controlTab === tab);
    const panel = this.panels.find((candidate) => candidate.dataset.controlPanel === tab);
    if (!button || !panel) return false;

    this.activeTab = tab;
    this.root.dataset.activeControlTab = tab;
    for (const candidate of this.buttons) {
      const selected = candidate === button;
      candidate.classList.toggle('active', selected);
      candidate.setAttribute('aria-selected', String(selected));
      candidate.tabIndex = selected ? 0 : -1;
    }
    for (const candidate of this.panels) {
      candidate.hidden = candidate !== panel;
    }
    button.scrollIntoView({ behavior: 'smooth', block: 'nearest', inline: 'nearest' });
    if (focus) button.focus();
    return true;
  }

  private bindEvents(storageKey?: string): void {
    for (const button of this.buttons) {
      button.addEventListener('click', () => {
        const tab = button.dataset.controlTab;
        if (!tab || !this.activate(tab)) return;
        if (storageKey) this.storeTab(storageKey, tab);
      });
      button.addEventListener('keydown', (event) => this.handleKeydown(event, storageKey));
    }

    this.root.querySelectorAll<HTMLButtonElement>('[data-control-tab-scroll]').forEach((button) => {
      button.addEventListener('click', () => {
        const direction = button.dataset.controlTabScroll === 'previous' ? -1 : 1;
        this.tabList.scrollBy({ left: direction * Math.max(110, this.tabList.clientWidth * 0.7), behavior: 'smooth' });
      });
    });
  }

  private handleKeydown(event: KeyboardEvent, storageKey?: string): void {
    const currentIndex = this.buttons.indexOf(event.currentTarget as HTMLButtonElement);
    if (currentIndex < 0) return;
    let nextIndex: number | undefined;
    if (event.key === 'ArrowLeft') nextIndex = (currentIndex - 1 + this.buttons.length) % this.buttons.length;
    if (event.key === 'ArrowRight') nextIndex = (currentIndex + 1) % this.buttons.length;
    if (event.key === 'Home') nextIndex = 0;
    if (event.key === 'End') nextIndex = this.buttons.length - 1;
    if (nextIndex === undefined) return;

    event.preventDefault();
    const tab = this.buttons[nextIndex]!.dataset.controlTab!;
    this.activate(tab, true);
    if (storageKey) this.storeTab(storageKey, tab);
  }

  private readStoredTab(storageKey: string): string | undefined {
    try {
      return localStorage.getItem(storageKey) ?? undefined;
    } catch {
      return undefined;
    }
  }

  private storeTab(storageKey: string, tab: string): void {
    try {
      localStorage.setItem(storageKey, tab);
    } catch {
      // Storage can be unavailable in privacy-restricted browser contexts.
    }
  }
}
