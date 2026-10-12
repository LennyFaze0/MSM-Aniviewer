import type { AnimationDocument } from './types';
import type { BinFormat, BinRevision, BinRevisionMode, ParsedBin } from './bin-parser';

interface WorkerResult {
  id: number;
  document?: AnimationDocument;
  revision?: BinRevision;
  format?: BinFormat;
  error?: string;
}

interface PendingRequest {
  resolve: (value: ParsedBin) => void;
  reject: (reason: Error) => void;
}

export class BinParserWorkerClient {
  private readonly worker = new Worker(new URL('../workers/bin-parser.worker.ts', import.meta.url), {
    type: 'module',
  });
  private readonly pending = new Map<number, PendingRequest>();
  private nextId = 1;
  private disposed = false;

  constructor() {
    this.worker.addEventListener('message', (event: MessageEvent<WorkerResult>) => {
      const request = this.pending.get(event.data.id);
      if (!request) return;
      this.pending.delete(event.data.id);
      if (event.data.document && event.data.revision && event.data.format) {
        request.resolve({
          document: event.data.document,
          revision: event.data.revision,
          format: event.data.format,
        });
      } else {
        request.reject(new Error(event.data.error || 'BIN parser worker failed.'));
      }
    });
    this.worker.addEventListener('error', (event) => {
      const error = new Error(event.message || 'BIN parser worker crashed.');
      this.pending.forEach((request) => request.reject(error));
      this.pending.clear();
    });
  }

  async parse(file: Blob, mode: BinRevisionMode, path: string): Promise<ParsedBin> {
    const id = this.nextId++;
    const buffer = await file.arrayBuffer();
    if (this.disposed) throw new Error('The previous asset library was closed.');
    const result = new Promise<ParsedBin>((resolve, reject) => {
      this.pending.set(id, { resolve, reject });
    });
    this.worker.postMessage({ id, buffer, mode, path }, [buffer]);
    return result;
  }

  dispose(): void {
    this.disposed = true;
    this.worker.terminate();
    this.pending.forEach((request) => request.reject(new Error('The previous asset library was closed.')));
    this.pending.clear();
  }
}
