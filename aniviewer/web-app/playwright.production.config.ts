import { defineConfig } from '@playwright/test';
import base from './playwright.config';

const deployedURL = process.env.ANIVIEWER_DEPLOYED_URL;
const previewURL = 'http://127.0.0.1:4175/MSM-Aniviewer/';

export default defineConfig({
  ...base,
  testDir: './smoke',
  use: { ...base.use, baseURL: deployedURL || previewURL },
  webServer: deployedURL ? undefined : {
    command: 'node node_modules/vite/bin/vite.js preview --host 127.0.0.1 --port 4175 --strictPort',
    url: previewURL,
    reuseExistingServer: false,
    timeout: 60_000,
  },
});
