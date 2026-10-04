import { test, expect, type Page } from '@playwright/test';

// E2E against the REAL backend via the vite preview proxy.
//
// AUTH MODEL (important): production OIDC/SSO is NOT exercised here and MUST NOT
// be claimed as verified. This environment has no OIDC; we use the loopback-only
// POST /auth/local/dev-token. That endpoint must be disabled outside 127.0.0.1
// (FROZEN_CONTRACT §5.1). The shared local secret is read from E2E_LOCAL_TOKEN.
//
// Required env:
//   E2E_API_TARGET     backend origin, e.g. http://127.0.0.1:8030 (proxied by preview)
//   E2E_LOCAL_TOKEN     shared secret accepted by /auth/local/dev-token
//   E2E_OWNER_SUB       owner sub to bind (optional, default "e2e-owner")

const LOCAL_TOKEN = process.env.E2E_LOCAL_TOKEN ?? '';

// Extract a cookie name=value pair from a raw Set-Cookie header string.
function extractCookie(setCookie: string | undefined, name: string): string | null {
  if (!setCookie) return null;
  for (const part of setCookie.split(/,(?=[^;]+=)/)) {
    const seg = part.trim();
    if (seg.startsWith(`${name}=`)) {
      return seg.slice(name.length + 1).split(';')[0];
    }
  }
  return null;
}

async function loginViaDevToken(page: Page): Promise<void> {
  // Backend LocalDevTokenRequest is a strict schema: only the `token` field.
  // We POST directly (same-origin via preview proxy), then explicitly lift the
  // HttpOnly session cookie into the browser context. The app reads the CSRF
  // token from its own /auth/me response and echoes it on writes (X-CSRF-Token).
  const res = await page.request.post('/auth/local/dev-token', {
    data: { token: LOCAL_TOKEN },
  });
  if (!res.ok()) {
    throw new Error(
      `dev-token login failed: HTTP ${res.status()} — set E2E_LOCAL_TOKEN and ensure the local dev-token endpoint is up on loopback`,
    );
  }
  const setCookie = res.headers()['set-cookie'];
  const session = extractCookie(setCookie, 'fy_session');
  if (session) {
    await page.context().addCookies([
      { name: 'fy_session', value: session, domain: '127.0.0.1', path: '/', httpOnly: true, sameSite: 'Lax' },
    ]);
  }
}

test.describe('anonymous access', () => {
  test('private route redirects to login and leaks no private data', async ({ page }) => {
    await page.goto('/chat');
    await expect(page).toHaveURL(/\/login/);
    // No conversation/message bubble should be present while unauthenticated.
    await expect(page.locator('.chat-scroll')).toHaveCount(0);
    await expect(page.getByRole('button', { name: /OIDC/ })).toBeVisible();
  });
});

test.describe('authenticated flows', () => {
  test.skip(!LOCAL_TOKEN, 'E2E_LOCAL_TOKEN not set; skipping real-session tests');

  test('dev-token login shows shell, navigation works, logout clears session+storage', async ({ page }) => {
    await loginViaDevToken(page);
    await page.goto('/chat');

    // Authenticated shell: the approved rail splits into two spaces, and the
    // active space follows the route. /chat is a personal-space route.
    const personalSpace = page.getByRole('button', { name: /个人空间/ });
    const workbenchSpace = page.getByRole('button', { name: /工作台空间/ });
    await expect(personalSpace).toHaveAttribute('aria-pressed', 'true');
    await expect(page.getByRole('navigation', { name: '主导航' })).toBeVisible();

    // Navigate across pages without a hard crash.
    await page.getByRole('link', { name: '历史' }).click();
    await expect(page).toHaveURL(/\/history/);

    // Switching to the workbench space keeps its entries reachable.
    await workbenchSpace.click();
    await expect(page.getByRole('link', { name: '审批中心' })).toBeVisible();
    await expect(page.getByRole('link', { name: '设置与数据' })).toBeVisible();
    await page.getByRole('link', { name: '设置与数据' }).click();
    await expect(page).toHaveURL(/\/settings/);

    // Logout: button in sidebar. The backend must actually invalidate the session.
    const logoutP = page.waitForResponse((r) => r.url().includes('/auth/logout'));
    await page.getByRole('button', { name: '登出' }).click();
    await expect((await logoutP).status()).toBe(200);
    await expect(page).toHaveURL(/\/login/);

    // Session cookie is gone / /auth/me rejects.
    const me = await page.request.get('/auth/me');
    expect([401, 403]).toContain(me.status());

    // Sensitive client storage was purged on logout.
    const ls = await page.evaluate(() => window.localStorage.length);
    expect(ls).toBe(0);
  });

  test('mobile viewport: shell and nav usable', async ({ page }, testInfo) => {
    testInfo.skip(testInfo.project.name !== 'mobile', 'mobile viewport only');
    await loginViaDevToken(page);
    await page.goto('/chat');
    // Primary nav (wrapped row on mobile) is reachable.
    await expect(page.getByRole('navigation', { name: '主导航' })).toBeVisible();
    await expect(page.getByRole('button', { name: /发送/ })).toBeAttached();
  });

  test('offline: composer disabled and offline notice shown', async ({ page, context }) => {
    await loginViaDevToken(page);
    await page.goto('/chat');
    await expect(page.getByRole('navigation', { name: '主导航' })).toBeVisible();
    await context.setOffline(true);
    // OfflineBadge reacts to the browser 'offline' event; no reload (which would
    // itself fail with ERR_INTERNET_DISCONNECTED).
    await expect(page.getByText(/offline|离线/i).first()).toBeVisible();
    const sendBtn = page.getByRole('button', { name: /发送/ });
    await expect(sendBtn).toBeDisabled();
    await context.setOffline(false);
  });

  test('assessment with missing answers does not produce a result', async ({ page }) => {
    await loginViaDevToken(page);
    await page.goto('/assessments');

    const startBtn = page.getByRole('button', { name: /开始测评/ });
    try {
      await startBtn.first().waitFor({ timeout: 8000 });
    } catch {
      test.skip(true, 'no assessment catalog seeded on backend');
    }
    await startBtn.first().click();

    // Session page renders the missing-item form. Leave all items unanswered,
    // then attempt submit.
    const submit = page.getByRole('button', { name: /提交/ });
    await submit.click();

    // Either a client-side warning about missing answers, or the server rejected;
    // never a rendered result block.
    const hasWarning = await page.getByText(/漏答|未答|missing/i).count();
    expect(hasWarning).toBeGreaterThan(0);
    await expect(page.locator('.notice.warn').filter({ hasText: /caveat|注意/i })).toHaveCount(0);
  });

  test('seeded pending merge/release proposal shows not-yet-merged wording', async ({ page }) => {
    await loginViaDevToken(page);
    await page.goto('/approvals');

    const pendingMerge = page.locator('.card', { hasText: '尚未合并/发布' });
    const executedDone = page.locator('.card', { hasText: '外部操作已执行' });

    // Wait for the proposal list to load before deciding whether to skip.
    await page.waitForLoadState('networkidle').catch(() => undefined);
    try {
      await page.locator('.card').first().waitFor({ timeout: 8000 });
    } catch {
      test.skip(true, 'no merge/release proposals seeded on backend');
    }
    if ((await pendingMerge.count()) === 0 && (await executedDone.count()) === 0) {
      test.skip(true, 'no merge/release proposals seeded on backend');
    }
    // A merge/release proposal must NEVER read as already merged/released before
    // the external side effect has executed. The page's intro copy intentionally
    // quotes the forbidden wording, so check only per-proposal status badges.
    const falselyDone = page.locator('.card .badge', { hasText: /已合并|已发布/ });
    await expect(falselyDone).toHaveCount(0);
    // The pending merge/release wording must actually be present.
    await expect(pendingMerge.first()).toBeVisible();
  });

  test('navigation to canvas and profiles works without errors', async ({ page }) => {
    await loginViaDevToken(page);
    // /canvas now opens the 19 号 team designer; the 05 号 read-only topology
    // canvas stays reachable as the second tab rather than being deleted.
    await page.goto('/canvas');
    await expect(page.getByRole('tab', { name: /内部团队/ })).toHaveAttribute('aria-selected', 'true');
    await page.getByRole('tab', { name: /协作拓扑与派发记录/ }).click();
    await expect(page.getByText('多 Agent 协作可视化画布')).toBeVisible();

    await page.goto('/profiles');
    await expect(page.getByText('个人与对象多维画像')).toBeVisible();
  });
});
