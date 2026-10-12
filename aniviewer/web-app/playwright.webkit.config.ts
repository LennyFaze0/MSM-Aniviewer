import { defineConfig, devices } from '@playwright/test';
import base from './playwright.config';

export default defineConfig({
  ...base,
  use: {
    ...base.use,
    launchOptions: process.env.ANIVIEWER_WEBKIT_EXECUTABLE
      ? { executablePath: process.env.ANIVIEWER_WEBKIT_EXECUTABLE } : undefined,
  },
  projects: [
    { name: 'desktop-webkit', use: { ...devices['Desktop Safari'] } },
    { name: 'mobile-webkit', use: { ...devices['iPhone 13'] } },
  ],
});
