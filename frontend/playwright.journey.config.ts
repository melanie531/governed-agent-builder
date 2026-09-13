import {defineConfig} from '@playwright/test';
export default defineConfig({
  testDir: './journey-e2e', workers: 1, timeout: 60000,
  expect: {timeout: 15000}, reporter: [['list']],
  outputDir: '../artifacts/journey-browser',
  use: {baseURL: 'http://127.0.0.1:5189', channel: process.env.PLAYWRIGHT_CHANNEL === 'chromium' ? undefined : 'chrome', headless: true,
    viewport: {width: 1440, height: 1080}, screenshot: 'only-on-failure', trace: 'retain-on-failure'},
  webServer: {command: 'cd .. && PUBLIC_URL=http://127.0.0.1:5189 .venv/bin/python scripts/journey_e2e_server.py',
    url: 'http://127.0.0.1:5189/api/demo/personas', reuseExistingServer: false, timeout: 30000},
});
