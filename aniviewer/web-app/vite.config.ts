import { defineConfig } from 'vite';

export default defineConfig({
  base: '/MSM-Aniviewer/',
  server: {
    // Reserved test domain mapped to loopback by the HTTP/mobile integration test, not a public host.
    allowedHosts: ['aniviewer.test'],
    fs: {
      allow: ['..'],
    },
  },
  optimizeDeps: {
    // Prebundle worker/lazy encoders up front: discovering them during an export otherwise reloads the dev viewer.
    include: ['mediabunny', 'gifenc', '@mediabunny/aac-encoder'],
    // unityfs-js contains a Vite inline-worker import. Keeping it out of the
    // prebundle lets Vite transform that worker correctly on Windows paths.
    exclude: ['unityfs-js'],
  },
  worker: { format: 'es' },
  build: {
    target: 'es2022',
    sourcemap: true,
    reportCompressedSize: true,
  },
});
