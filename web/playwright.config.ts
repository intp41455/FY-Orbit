import { defineConfig, devices } from '@playwright/test';

// E2E specs drive the real built app against a real backend API. Because the
// Runtime slice (FastAPI + DB) is not yet available, `npm run test:e2e` is
// implemented but MUST NOT be reported as passing until the backend is up.
// See evidence/g5/ — E2E is listed as BLOCKED_EXTERNAL until then.
export default defineConfig({
  testDir: './e2e',
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: 0,
  workers: 1,
  reporter: [['list'], ['json', { outputFile: '../evidence/g5/e2e-results.json' }]],
  use: {
    baseURL: process.env.E2E_BASE_URL ?? 'http://127.0.0.1:5173',
    trace: 'on-first-retry',
  },
  projects: [
    { name: 'desktop', use: { ...devices['Desktop Chrome'] } },
    { name: 'mobile', use: { ...devices['Pixel 5'] } },
  ],
  webServer: {
    command: 'npm run preview -- --host 127.0.0.1 --port 5173',
    url: 'http://127.0.0.1:5173',
    reuseExistingServer: true,
    timeout: 120_000,
  },
});
