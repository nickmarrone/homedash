import { defineConfig } from '@playwright/test';

const STATIC_PORT = 4173;

// Real Chrome rather than Playwright's bundled Chromium, because the wall panel
// runs Chrome-family and `tools/panel/setup.sh` documents why the download is
// not always reachable. ubuntu-latest ships it.
const chrome = {
  channel: 'chrome',
  launchOptions: { args: ['--no-sandbox', '--disable-dev-shm-usage'] },
};

export default defineConfig({
  testDir: './tests',
  outputDir: './shots/test-results',
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: 0,
  reporter: process.env.CI ? [['list'], ['github']] : 'list',
  use: { ...chrome, baseURL: `http://127.0.0.1:${STATIC_PORT}`, trace: 'retain-on-failure' },
  webServer: {
    command: `node tests/support/serve-build.mjs ${STATIC_PORT}`,
    url: `http://127.0.0.1:${STATIC_PORT}/`,
    reuseExistingServer: !process.env.CI,
  },
  // The two orientations the panel is really mounted in. Everything the API
  // stub drives runs in both; the real-backend run only needs one.
  projects: [
    {
      name: 'landscape',
      testIgnore: /real-backend/,
      use: { viewport: { width: 1920, height: 1080 } },
    },
    {
      name: 'portrait',
      testIgnore: /real-backend/,
      use: { viewport: { width: 1080, height: 1920 } },
    },
    {
      name: 'real-backend',
      testMatch: /real-backend/,
      fullyParallel: false,
      use: { viewport: { width: 1920, height: 1080 } },
    },
  ],
});
