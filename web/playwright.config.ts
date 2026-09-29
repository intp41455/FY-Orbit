import { defineConfig, devices } from '@playwright/test';

// E2E drives the production-built app (vite preview) against the REAL backend.
// The preview server proxies /api,/auth,/health,/metrics to E2E_API_TARGET so
// the browser sees a single same-origin origin. Auth uses the loopback
// dev-token endpoint ONLY (no OIDC/SSO in this environment) — see e2e specs.
const PREVIEW_PORT = Number(process.env.E2E_PREVIEW_PORT ?? 4173);
const PREVIEW_URL = `http://127.0.0.1:${PREVIEW_PORT}`;

export default defineConfig({
  testDir: './e2e',
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: 0,
  workers: 1,
  reporter: [['list'], ['json', { outputFile: '../evidence/g5/e2e-results.json' }]],
  use: {
    baseURL: process.env.E2E_BASE_URL ?? PREVIEW_URL,
    trace: 'on-first-retry',
  },
  projects: [
    { name: 'desktop', use: { ...devices['Desktop Chrome'] } },
    { name: 'mobile', use: { ...devices['Pixel 5'] } },
  ],
  webServer: {
    // E2E_API_TARGET is inherited from the shell environment.
    command: 'npm run preview',
    url: PREVIEW_URL,
    reuseExistingServer: true,
    timeout: 120_000,
    env: {
      E2E_API_TARGET: process.env.E2E_API_TARGET ?? 'http://127.0.0.1:8030',
      E2E_PREVIEW_PORT: String(PREVIEW_PORT),
    },
  },
});
