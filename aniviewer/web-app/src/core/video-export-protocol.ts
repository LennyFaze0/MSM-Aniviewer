export type VideoExportFormat = 'auto' | 'mp4' | 'webm' | 'mov';

export interface ExportAudioPcm {
  sampleRate: number;
  channels: Float32Array<ArrayBuffer>[];
}

export interface VideoExportInit {
  format: VideoExportFormat;
  width: number;
  height: number;
  frameRate: number;
  bitrate: number;
  duration: number;
  audio?: ExportAudioPcm;
}

export interface VideoEncoderSelection {
  format: 'mp4' | 'webm' | 'mov';
  videoCodec: 'avc' | 'vp9' | 'vp8';
  audioCodec?: 'aac' | 'opus';
  hardwareAcceleration: 'prefer-hardware' | 'no-preference';
}

export type VideoWorkerCommand =
  | { type: 'init'; options: VideoExportInit }
  | { type: 'frame'; frame: VideoFrame }
  | { type: 'finish' };

export type VideoWorkerRequest = VideoWorkerCommand & { id: number };
export interface VideoWorkerReply {
  id: number;
  error?: string;
  selection?: VideoEncoderSelection;
  buffer?: ArrayBuffer;
}

// The encoded output is held in memory until download. This is not a streaming-to-disk exporter.
export const MAX_VIDEO_EXPORT_BYTES = 128 * 1024 * 1024;
export const MAX_EXPORT_PCM_BYTES = 128 * 1024 * 1024;
