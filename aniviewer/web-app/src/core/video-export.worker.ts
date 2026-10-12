import {
  AudioSample, AudioSampleSource, BufferTarget, canEncodeAudio, canEncodeVideo,
  Mp4OutputFormat, MovOutputFormat, Output, Quality, VideoSample, VideoSampleSource, WebMOutputFormat,
} from 'mediabunny';
import {
  MAX_VIDEO_EXPORT_BYTES,
  type VideoEncoderSelection, type VideoExportInit, type VideoWorkerReply, type VideoWorkerRequest,
} from './video-export-protocol';

let output: Output | undefined;
let target: BufferTarget;
let video: VideoSampleSource;
let audio: AudioSampleSource | undefined;
let options: VideoExportInit;
let audioOffset = 0;
let encodedBytes = 0;
let failed = false;

async function chooseEncoder(config: VideoExportInit): Promise<VideoEncoderSelection> {
  const candidates: VideoEncoderSelection[] = [];
  for (const format of config.format === 'auto' ? ['mp4', 'webm'] as const : [config.format]) {
    for (const codec of format !== 'webm' ? ['avc'] as const : ['vp9', 'vp8'] as const) {
      for (const hardwareAcceleration of ['prefer-hardware', 'no-preference'] as const) {
        candidates.push({ format, videoCodec: codec, audioCodec: config.audio ? (format !== 'webm' ? 'aac' : 'opus') : undefined, hardwareAcceleration });
      }
    }
  }
  for (const candidate of candidates) {
    if (!await canEncodeVideo(candidate.videoCodec, {
      width: config.width, height: config.height, quality: new Quality({ bitrate: config.bitrate }),
      hardwareAcceleration: candidate.hardwareAcceleration, latencyMode: 'quality',
    })) continue;
    const audioOptions = {
      numberOfChannels: config.audio?.channels.length ?? 2, sampleRate: config.audio?.sampleRate ?? 48_000,
      quality: new Quality({ bitrate: 192_000 }),
    };
    if (candidate.audioCodec && !await canEncodeAudio(candidate.audioCodec, audioOptions)) {
      if (candidate.audioCodec !== 'aac') continue;
      const { registerAacEncoder } = await import('@mediabunny/aac-encoder');
      registerAacEncoder();
      if (!await canEncodeAudio('aac', audioOptions)) continue;
    }
    return candidate;
  }
  throw new Error(`No compatible ${config.format === 'auto' ? 'MP4 or WebM' : config.format.toUpperCase()} encoder is available for these dimensions${config.audio ? ' and audio' : ''}. Try Auto format, a lower resolution, or disable Include matched audio.`);
}

function countPacket(packet: { data: Uint8Array }): void {
  encodedBytes += packet.data.byteLength;
  if (encodedBytes > MAX_VIDEO_EXPORT_BYTES) throw new Error('Encoded video exceeds the 128 MiB memory limit. Lower the bitrate.');
}

async function encodeAudioThrough(time: number): Promise<void> {
  const pcm = options.audio;
  if (!audio || !pcm) return;
  const end = Math.min(pcm.channels[0]!.length, Math.round(Math.min(options.duration, time) * pcm.sampleRate));
  while (audioOffset < end) {
    const length = Math.min(pcm.sampleRate, end - audioOffset);
    const data = new Float32Array(length * pcm.channels.length);
    pcm.channels.forEach((channel, index) => data.set(channel.subarray(audioOffset, audioOffset + length), index * length));
    const sample = new AudioSample({ data, format: 'f32-planar', sampleRate: pcm.sampleRate, numberOfChannels: pcm.channels.length, timestamp: audioOffset / pcm.sampleRate });
    try { await audio.add(sample); } finally { sample.close(); }
    audioOffset += length;
  }
}

async function handle(message: VideoWorkerRequest): Promise<void> {
  const reply: VideoWorkerReply = { id: message.id };
  if (message.type === 'init') {
    options = message.options;
    const selection = await chooseEncoder(options);
    target = new BufferTarget();
    output = new Output({ target, format: selection.format === 'mov' ? new MovOutputFormat({ fastStart: 'in-memory' })
      : selection.format === 'mp4' ? new Mp4OutputFormat({ fastStart: 'in-memory' }) : new WebMOutputFormat() });
    video = new VideoSampleSource({
      codec: selection.videoCodec, quality: new Quality({ bitrate: options.bitrate }),
      hardwareAcceleration: selection.hardwareAcceleration, latencyMode: 'quality', alpha: 'discard',
      onEncodedPacket: countPacket,
    });
    // Explicit sample timestamps preserve a partial final frame; do not quantize it to another full frame.
    output.addVideoTrack(video);
    if (selection.audioCodec) {
      audio = new AudioSampleSource({ codec: selection.audioCodec, quality: new Quality({ bitrate: 192_000 }), onEncodedPacket: countPacket });
      output.addAudioTrack(audio);
    }
    await output.start();
    reply.selection = selection;
  } else if (message.type === 'frame') {
    const sample = new VideoSample(message.frame);
    try {
      await encodeAudioThrough((message.frame.timestamp + (message.frame.duration ?? 0)) / 1_000_000);
      await video.add(sample);
    } finally { sample.close(); message.frame.close(); }
  } else {
    await encodeAudioThrough(options.duration);
    await output!.finalize();
    reply.buffer = target.buffer!;
  }
  postMessage(reply, reply.buffer ? [reply.buffer] : []);
}

// Serialize commands even while an encoder is applying backpressure.
let queue = Promise.resolve();
self.onmessage = ({ data }: MessageEvent<VideoWorkerRequest>) => {
  queue = queue.then(async () => {
    if (failed) { if (data.type === 'frame') data.frame.close(); return; }
    try { await handle(data); }
    catch (error) {
      failed = true;
      if (data.type === 'frame') data.frame.close();
      postMessage({ id: data.id, error: error instanceof Error ? error.message : String(error) } satisfies VideoWorkerReply);
      await output?.cancel().catch(() => undefined);
    }
  });
};
