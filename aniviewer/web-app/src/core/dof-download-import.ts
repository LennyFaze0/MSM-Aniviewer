import type { DofManifestEntry } from './dof-asset-grabber';

const HANDLE_DATABASE = 'msm-aniviewer-download-handles';
const HANDLE_STORE = 'handles';
const HANDLE_KEY = 'dof-downloads';

export interface DofDownloadFileHandle {
  kind: 'file';
  name: string;
  getFile(): Promise<File>;
}

export interface DofDownloadDirectoryEntry {
  kind: 'directory';
  name: string;
}

export interface DofDownloadDirectoryHandle {
  kind: 'directory';
  name: string;
  values(): AsyncIterableIterator<DofDownloadFileHandle | DofDownloadDirectoryEntry>;
  queryPermission?(options?: { mode: 'read' }): Promise<PermissionState>;
  requestPermission?(options?: { mode: 'read' }): Promise<PermissionState>;
}

export type DofDownloadSnapshot = Map<string, string>;

export interface DofDownloadScanResult {
  files: File[];
  missing: DofManifestEntry[];
}

interface DirectoryPickerWindow extends Window {
  showDirectoryPicker?: (options?: {
    id?: string;
    mode?: 'read';
    startIn?: 'downloads';
  }) => Promise<DofDownloadDirectoryHandle>;
}

interface WaitForDofDownloadsOptions {
  baseline?: DofDownloadSnapshot;
  intervalMs?: number;
  timeoutMs?: number;
  signal?: AbortSignal;
  onProgress?: (found: number, total: number) => void;
}

function normalizedName(name: string): string {
  return name.trim().toLowerCase();
}

function escapeRegExp(value: string): string {
  return value.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}

function fileSignature(file: File): string {
  return `${file.size}:${file.lastModified}`;
}

function abortError(): DOMException {
  return new DOMException('Download import was cancelled.', 'AbortError');
}

function delay(milliseconds: number, signal?: AbortSignal): Promise<void> {
  if (signal?.aborted) return Promise.reject(abortError());
  return new Promise((resolve, reject) => {
    const timeout = window.setTimeout(() => {
      signal?.removeEventListener('abort', cancel);
      resolve();
    }, milliseconds);
    const cancel = (): void => {
      window.clearTimeout(timeout);
      signal?.removeEventListener('abort', cancel);
      reject(abortError());
    };
    signal?.addEventListener('abort', cancel, { once: true });
  });
}

function openHandleDatabase(): Promise<IDBDatabase> {
  return new Promise((resolve, reject) => {
    const request = indexedDB.open(HANDLE_DATABASE, 1);
    request.onupgradeneeded = () => {
      const database = request.result;
      if (!database.objectStoreNames.contains(HANDLE_STORE)) database.createObjectStore(HANDLE_STORE);
    };
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error ?? new Error('Could not open the download handle store.'));
  });
}

async function readStoredHandle(): Promise<DofDownloadDirectoryHandle | undefined> {
  const database = await openHandleDatabase();
  try {
    return await new Promise((resolve, reject) => {
      const request = database.transaction(HANDLE_STORE, 'readonly').objectStore(HANDLE_STORE).get(HANDLE_KEY);
      request.onsuccess = () => resolve(request.result as DofDownloadDirectoryHandle | undefined);
      request.onerror = () => reject(request.error ?? new Error('Could not restore the downloads folder.'));
    });
  } finally {
    database.close();
  }
}

async function writeStoredHandle(handle: DofDownloadDirectoryHandle): Promise<void> {
  const database = await openHandleDatabase();
  try {
    await new Promise<void>((resolve, reject) => {
      const transaction = database.transaction(HANDLE_STORE, 'readwrite');
      transaction.objectStore(HANDLE_STORE).put(handle, HANDLE_KEY);
      transaction.oncomplete = () => resolve();
      transaction.onerror = () => reject(transaction.error ?? new Error('Could not remember the downloads folder.'));
      transaction.onabort = () => reject(transaction.error ?? new Error('Could not remember the downloads folder.'));
    });
  } finally {
    database.close();
  }
}

export function supportsDofDownloadImport(): boolean {
  return typeof window !== 'undefined'
    && typeof (window as DirectoryPickerWindow).showDirectoryPicker === 'function';
}

export async function loadStoredDofDownloadDirectory(): Promise<DofDownloadDirectoryHandle | undefined> {
  if (!supportsDofDownloadImport() || typeof indexedDB === 'undefined') return undefined;
  try {
    return await readStoredHandle();
  } catch {
    return undefined;
  }
}

export async function requestDofDownloadDirectory(
  existing?: DofDownloadDirectoryHandle,
): Promise<DofDownloadDirectoryHandle> {
  if (!supportsDofDownloadImport()) throw new Error('Automatic download import is not supported by this browser.');
  if (existing) {
    const currentPermission = existing.queryPermission
      ? await existing.queryPermission({ mode: 'read' })
      : 'granted';
    if (currentPermission === 'granted') return existing;
    if (currentPermission !== 'denied' && existing.requestPermission) {
      const requestedPermission = await existing.requestPermission({ mode: 'read' });
      if (requestedPermission === 'granted') return existing;
    }
  }

  const picker = (window as DirectoryPickerWindow).showDirectoryPicker;
  if (!picker) throw new Error('Automatic download import is not supported by this browser.');
  const selected = await picker.call(window, {
    id: 'aniviewer-dof-downloads',
    mode: 'read',
    startIn: 'downloads',
  });
  if (typeof indexedDB !== 'undefined') {
    void writeStoredHandle(selected).catch(() => {
      // The active session can still use the granted handle when persistence is unavailable.
    });
  }
  return selected;
}

export function matchesDownloadedBundleName(candidate: string, expected: string): boolean {
  const dot = expected.lastIndexOf('.');
  const stem = dot > 0 ? expected.slice(0, dot) : expected;
  const extension = dot > 0 ? expected.slice(dot) : '';
  const pattern = `^${escapeRegExp(stem)}(?: \\(\\d+\\))?${escapeRegExp(extension)}$`;
  return new RegExp(pattern, 'i').test(candidate);
}

async function matchingDirectoryFiles(
  directory: DofDownloadDirectoryHandle,
  entries: DofManifestEntry[],
): Promise<Array<{ entry: DofManifestEntry; file: File }>> {
  const matches: Array<{ entry: DofManifestEntry; file: File }> = [];
  for await (const handle of directory.values()) {
    if (handle.kind !== 'file') continue;
    const entry = entries.find((candidate) => matchesDownloadedBundleName(handle.name, candidate.name));
    if (!entry) continue;
    try {
      const file = await handle.getFile();
      if (entry.size >= 0 && file.size !== entry.size) continue;
      matches.push({ entry, file });
    } catch {
      // A browser may briefly lock a file while moving a completed download into place.
    }
  }
  return matches;
}

export async function captureDofDownloadSnapshot(
  directory: DofDownloadDirectoryHandle,
  entries: DofManifestEntry[],
): Promise<DofDownloadSnapshot> {
  const snapshot: DofDownloadSnapshot = new Map();
  const matches = await matchingDirectoryFiles(directory, entries);
  matches.forEach(({ file }) => snapshot.set(normalizedName(file.name), fileSignature(file)));
  return snapshot;
}

export async function scanDownloadedDofBundles(
  directory: DofDownloadDirectoryHandle,
  entries: DofManifestEntry[],
  baseline: DofDownloadSnapshot = new Map(),
): Promise<DofDownloadScanResult> {
  const newest = new Map<string, File>();
  const matches = await matchingDirectoryFiles(directory, entries);
  matches.forEach(({ entry, file }) => {
    if (baseline.get(normalizedName(file.name)) === fileSignature(file)) return;
    const key = normalizedName(entry.name);
    const current = newest.get(key);
    if (!current || file.lastModified >= current.lastModified) newest.set(key, file);
  });

  const files: File[] = [];
  const missing: DofManifestEntry[] = [];
  entries.forEach((entry) => {
    const downloaded = newest.get(normalizedName(entry.name));
    if (!downloaded) {
      missing.push(entry);
      return;
    }
    files.push(downloaded.name === entry.name
      ? downloaded
      : new File([downloaded], entry.name, {
        type: downloaded.type || 'application/octet-stream',
        lastModified: downloaded.lastModified,
      }));
  });
  return { files, missing };
}

export async function waitForDownloadedDofBundles(
  directory: DofDownloadDirectoryHandle,
  entries: DofManifestEntry[],
  options: WaitForDofDownloadsOptions = {},
): Promise<File[]> {
  const intervalMs = options.intervalMs ?? 650;
  const timeoutMs = options.timeoutMs ?? 10 * 60 * 1000;
  const deadline = Date.now() + timeoutMs;
  let lastFound = -1;

  while (Date.now() <= deadline) {
    if (options.signal?.aborted) throw abortError();
    const result = await scanDownloadedDofBundles(directory, entries, options.baseline);
    if (result.files.length !== lastFound) {
      lastFound = result.files.length;
      options.onProgress?.(result.files.length, entries.length);
    }
    if (!result.missing.length) return result.files;
    await delay(intervalMs, options.signal);
  }

  const final = await scanDownloadedDofBundles(directory, entries, options.baseline);
  const names = final.missing.slice(0, 3).map((entry) => entry.name).join(', ');
  const remainder = final.missing.length > 3 ? ` and ${final.missing.length - 3} more` : '';
  throw new Error(`Timed out waiting for ${names}${remainder}. Use Open files to import the downloads manually.`);
}
