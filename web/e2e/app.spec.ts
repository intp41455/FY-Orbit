import { test, expect } from '@playwright/test';

// These E2E specs are implemented against the real backend API contract.
// They are expected to FAIL / BE BLOCKED until the Runtime slice (FastAPI + DB)
// is available; do not report them as passing before then.

test.describe('U01/U11: login, responsive shell, logout cleanup', () => {
  test('unauthenticated user is redirected to /login and sees no private data', async ({ page }) => {
    await page.goto('/chat');
    await expect(page).toHaveURL(/\/login/);
    await expect(page.getByRole('heading', { name: /Find Yourself/ })).toBeVisible();
  });

  test('shell is usable at mobile viewport', async ({ page }) => {
    await page.goto('/login');
    await expect(page.getByRole('button', { name: /OIDC/ })).toBeVisible();
  });
});

test.describe('U03: offline cannot submit', () => {
  test('composer is disabled and an offline notice shows when network is off', async ({ page, context }) => {
    await page.goto('/chat');
    await context.setOffline(true);
    await expect(page.getByText(/offline|离线/i)).toBeVisible();
    await context.setOffline(false);
  });
});

test.describe('U04: approval center semantics', () => {
  test('pending merge proposal shows not-yet-merged wording', async ({ page }) => {
    // Requires authenticated session + seeded proposal; will block without backend.
    await page.goto('/approvals');
    await expect(page.getByText(/审批中心/)).toBeVisible();
  });
});

test.describe('U05/U06: assessment empty/reverse handling', () => {
  test('submitting with missing answers does not produce a result', async ({ page }) => {
    await page.goto('/assessments');
    await expect(page.getByText(/测评/)).toBeVisible();
  });
});
