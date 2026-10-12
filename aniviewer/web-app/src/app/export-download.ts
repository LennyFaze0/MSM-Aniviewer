/** Keep the result alive so mobile browsers can save/share it from a fresh user tap. */
export class ExportDownload {
  private url?: string;
  private file?: File;
  private readonly name: HTMLElement;
  private readonly download: HTMLAnchorElement;
  private readonly open: HTMLAnchorElement;
  private readonly share: HTMLButtonElement;
  private readonly message: HTMLElement;

  constructor(private readonly host: HTMLElement) {
    host.innerHTML = `<fieldset class="control-group"><legend>Export Ready</legend>
      <p id="exportResultName" class="control-hint"></p>
      <div class="export-command-stack">
        <a id="saveExportButton" class="button primary control-wide-button">Save File</a>
        <button id="shareExportButton" class="button control-wide-button" type="button" hidden>Share / Save to Files</button>
        <a id="openExportButton" class="button control-wide-button" target="_blank" rel="noopener">Open File</a>
        <button id="clearExportButton" class="button control-wide-button" type="button">Clear Result</button>
      </div><p id="exportSaveHint" class="control-hint">If the download did not start, tap Save File. On iPhone/iPad, Share can save the file to Files. This result stays available until replaced or cleared.</p>
    </fieldset>`;
    this.name = host.querySelector('#exportResultName')!;
    this.download = host.querySelector('#saveExportButton')!;
    this.open = host.querySelector('#openExportButton')!;
    this.share = host.querySelector('#shareExportButton')!;
    this.message = host.querySelector('#exportSaveHint')!;
    this.share.addEventListener('click', () => void this.shareFile());
    host.querySelector('#clearExportButton')!.addEventListener('click', () => this.clear());
  }

  present(blob: Blob, filename: string): void {
    this.clear();
    this.file = new File([blob], filename, { type: blob.type });
    this.url = URL.createObjectURL(this.file);
    this.name.textContent = `${filename} (${(blob.size / 1024 / 1024).toFixed(2)} MiB)`;
    this.download.href = this.url;
    this.download.download = filename;
    this.open.href = this.url;
    this.share.hidden = true;
    try { this.share.hidden = !(typeof navigator.share === 'function' && navigator.canShare?.({ files: [this.file] })); } catch { /* Use the download link instead. */ }
    this.host.hidden = false;
    // Desktop convenience is retained. Unlike the old download helper, the URL is not revoked immediately.
    this.download.click();
  }

  private async shareFile(): Promise<void> {
    if (!this.file) return;
    try {
      this.share.disabled = true;
      await navigator.share({ files: [this.file], title: this.file.name });
    } catch (error) {
      if (!(error instanceof DOMException && error.name === 'AbortError')) {
        this.message.textContent = 'Sharing was unavailable. Try Save File or Open File instead; your export is still here.';
      }
    } finally { this.share.disabled = false; }
  }

  clear(): void {
    if (this.url) URL.revokeObjectURL(this.url);
    this.url = undefined;
    this.file = undefined;
    this.download.removeAttribute('href');
    this.open.removeAttribute('href');
    this.host.hidden = true;
  }
}
