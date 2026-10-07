/**
 * Config for the post-release rollout smoke (e2e-accept/rollout-smoke.spec.ts).
 * Requires ACCEPT_ORIGIN (deployed application origin) and E2E_CRED; see the
 * spec header for all portable inputs. Serial, single worker, bounded actions.
 */
import {defineConfig} from '@playwright/test';

if (!process.env.ACCEPT_ORIGIN) throw new Error('Set ACCEPT_ORIGIN to the deployed application origin');

export default defineConfig({
  testDir: './e2e-accept', testMatch: 'rollout-smoke.spec.ts',
  fullyParallel: false, workers: 1, timeout: 300000,
  expect: {timeout: 20000}, reporter: [['list']],
  outputDir: process.env.SMOKE_EVIDENCE ? process.env.SMOKE_EVIDENCE + '/test-results' : './accept-evidence/test-results',
  use: {
    baseURL: process.env.ACCEPT_ORIGIN,
    channel: process.env.PLAYWRIGHT_CHANNEL === 'chromium' ? undefined : 'chrome',
    headless: true, viewport: {width: 1440, height: 1080},
    // No traces: sign-in actions would retain credential values on failure.
    screenshot: 'only-on-failure', trace: 'off',
  },
});
