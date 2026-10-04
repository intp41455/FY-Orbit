import { test, expect, type Page } from '@playwright/test';

/**
 * E2E for the 21 号 approved UI against the REAL backend.
 *
 * Scope:
 *  1. the approved shell (left vertical nav, workbench/personal spaces) renders
 *     and every planned entry is reachable — no feature removed for simplicity;
 *  2. the team designer drives the real /api/teams surface: create → validate →
 *     start → per-node model display → independent sessions → control → events;
 *  3. the terminal pane reattaches to a live server session after a reload;
 *  4. mobile reflow keeps the same functions instead of truncating them.
 *
 * This is SYNTHETIC verification: with no provider credentials the catalog
 * offers only the deterministic local model, and the spec asserts exactly that.
 * A real-model round trip is BLOCKED_EXTERNAL and is not claimed here.
 */

const LOCAL_TOKEN = process.env.E2E_LOCAL_TOKEN ?? '';

let csrfToken = '';

function extractCookie(setCookie: string | undefined, name: string): string | null {
  if (!setCookie) return null;
  for (const part of setCookie.split(/,(?=[^;]+=)/)) {
    const seg = part.trim();
    if (seg.startsWith(`${name}=`)) return seg.slice(name.length + 1).split(';')[0];
  }
  return null;
}

async function loginViaDevToken(page: Page): Promise<void> {
  const res = await page.request.post('/auth/local/dev-token', { data: { token: LOCAL_TOKEN } });
  if (!res.ok()) {
    throw new Error(`dev-token login failed: HTTP ${res.status()} — set E2E_LOCAL_TOKEN`);
  }
  const session = extractCookie(res.headers()['set-cookie'], 'fy_session');
  if (session) {
    await page.context().addCookies([
      { name: 'fy_session', value: session, domain: '127.0.0.1', path: '/', httpOnly: true, sameSite: 'Lax' },
    ]);
  }
  // The CSRF token is bound to THIS session, so raw API mutations must echo it
  // back; otherwise every write is rejected with 403.
  csrfToken = (await res.json()).csrf_token;
}

/** Raw API call carrying the session cookie and its bound CSRF token. */
async function api(
  page: Page,
  method: 'GET' | 'POST' | 'PUT' | 'PATCH' | 'DELETE',
  path: string,
  body?: unknown,
) {
  return page.request.fetch(path, {
    method,
    data: body === undefined ? undefined : JSON.stringify(body),
    headers: {
      'Content-Type': 'application/json',
      ...(csrfToken ? { 'X-CSRF-Token': csrfToken } : {}),
    },
  });
}

async function getJson<T>(page: Page, path: string): Promise<T> {
  const res = await api(page, 'GET', path);
  expect(res.ok(), `GET ${path} -> ${res.status()}`).toBeTruthy();
  return (await res.json()) as T;
}

/** Create a team through the real API so UI tests start from a known state. */
async function seedTeam(page: Page, name: string, extra: Record<string, unknown> = {}) {
  const res = await api(page, 'POST', '/api/teams', {
    name,
    template_id: 'engineering',
    budget_ref: { root_budget_usd: 0.5, member_reserve_cap_usd: 0.05 },
    reason: 'e2e seed',
    ...extra,
  });
  expect(res.status()).toBe(201);
  const { team } = await res.json();
  return team as { id: string; name: string; version: number };
}

async function openCanvas(page: Page) {
  await page.goto('/canvas');
  await expect(page.getByText('一个入口，组织你的 AI 团队')).toBeVisible();
}

/** Select a team tab in the designer toolbar. */
async function selectTeam(page: Page, name: string) {
  await page.locator('.tabs-container').getByRole('button', { name }).first().click();
}

/** The inspector aside; scoping keeps the member-node text unambiguous. */
function inspector(page: Page) {
  return page.getByRole('complementary', { name: '节点设置' });
}

function memberNode(page: Page, role: string) {
  return page.locator('.team-map').getByRole('button', { name: new RegExp(role) });
}

// ---------------------------------------------------------------- shell
test.describe('21 号已批准界面外壳', () => {
  test.beforeEach(async ({ page }) => {
    await loginViaDevToken(page);
  });

  test('左侧竖排导航与双空间切换可用，且不删功能', async ({ page }) => {
    await page.goto('/workbench');
    await expect(page.getByRole('navigation', { name: '主导航' })).toBeVisible();

    const workbenchSpace = page.getByRole('button', { name: /工作台空间/ });
    const personalSpace = page.getByRole('button', { name: /个人空间/ });
    await expect(workbenchSpace).toHaveAttribute('aria-pressed', 'true');
    await expect(personalSpace).toHaveAttribute('aria-pressed', 'false');

    // Workbench space keeps all of its planned entries.
    for (const label of ['任务工作台', '协作画布', 'Agent 与技能', '审批中心', '设置与数据']) {
      await expect(page.getByRole('link', { name: new RegExp(label) })).toBeVisible();
    }

    // Switching to the personal space keeps that space's entries too.
    await personalSpace.click();
    await expect(personalSpace).toHaveAttribute('aria-pressed', 'true');
    for (const label of ['对话', '历史', '成长记录', '测评', '多维画像']) {
      await expect(page.getByRole('link', { name: new RegExp(label) })).toBeVisible();
    }

    // Bottom global controls stay reachable (permission / budget).
    await expect(page.getByRole('link', { name: /权限与沙箱隔离/ })).toBeVisible();
    await expect(page.getByRole('link', { name: /预算与用量/ })).toBeVisible();
  });

  test('工作台空间的每个入口都能打开且无运行时报错', async ({ page }) => {
    const errors: string[] = [];
    page.on('pageerror', (e) => errors.push(e.message));
    await page.goto('/workbench');
    for (const [link, marker] of [
      ['任务工作台', '任务工作台'],
      ['协作画布', '一个入口，组织你的 AI 团队'],
      ['Agent 与技能', '能力目录'],
      ['审批中心', '审批'],
      ['设置与数据', '设置'],
    ] as const) {
      await page.getByRole('link', { name: new RegExp(link) }).click();
      await expect(page.getByText(marker).first()).toBeVisible();
    }
    expect(errors).toEqual([]);
  });

  test('个人空间的每个入口都能打开且无运行时报错', async ({ page }) => {
    const errors: string[] = [];
    page.on('pageerror', (e) => errors.push(e.message));
    await page.goto('/workbench');
    for (const [link, marker] of [
      ['对话', '对话'],
      ['历史', '历史'],
      ['成长记录', '成长'],
      ['测评', '测评'],
      ['多维画像', '画像'],
    ] as const) {
      await page.getByRole('button', { name: /个人空间/ }).click();
      await page.getByRole('link', { name: new RegExp(link) }).click();
      await expect(page.getByText(marker).first()).toBeVisible();
    }
    expect(errors).toEqual([]);
  });

  test('关键控件有键盘可达性与足够点击区域', async ({ page }) => {
    await openCanvas(page);
    await page.keyboard.press('Tab');
    await page.keyboard.press('Tab');
    const focused = await page.evaluate(() => document.activeElement?.tagName ?? '');
    expect(['A', 'BUTTON', 'SELECT', 'INPUT', 'TEXTAREA']).toContain(focused);

    const heights = await page.evaluate(() =>
      Array.from(
        document.querySelectorAll<HTMLElement>('.tabs-container button, .team-toolbar button'),
      ).map((el) => el.getBoundingClientRect().height),
    );
    expect(heights.length).toBeGreaterThan(0);
    for (const h of heights) expect(h).toBeGreaterThanOrEqual(32);
  });
});

// ---------------------------------------------------------------- teams
test.describe('19 号团队画布（真实 API）', () => {
  test.beforeEach(async ({ page }) => {
    await loginViaDevToken(page);
  });

  test('目录只提供真实可用的模型，并如实标注未配置', async ({ page }) => {
    await openCanvas(page);
    const catalog = await getJson<{
      real_model_configured: boolean;
      providers: { provider_id: string; credential_configured: boolean; credential_ref: string }[];
      models: { provider_id: string }[];
    }>(page, '/api/teams/catalog');

    // No provider credentials in this environment ⇒ no remote models offered.
    if (!catalog.real_model_configured) {
      expect(catalog.models.filter((m) => m.provider_id === 'openai-compatible')).toEqual([]);
      await expect(page.getByText(/真实模型未配置 · BLOCKED_EXTERNAL/)).toBeVisible();
      await expect(page.getByText(/真实模型往返为 BLOCKED_EXTERNAL/)).toBeVisible();
    }
    // Whatever is offered carries a reference, never a secret.
    for (const p of catalog.providers) {
      expect(typeof p.credential_configured).toBe('boolean');
      expect(p.credential_ref).toMatch(/^(env:|inprocess:)/);
    }
    expect(JSON.stringify(catalog)).not.toContain('sk-');
  });

  test('建队 → 校验 → 启动 → 逐节点模型与独立会话 → 事件', async ({ page }) => {
    await openCanvas(page);
    const name = `e2e-建队-${Date.now().toString(36)}`;

    await page.getByLabel('团队名称').fill(name);
    await page.getByRole('button', { name: '新建团队草稿' }).click();
    await expect(page.getByText(/草稿已创建/)).toBeVisible();

    const list = await getJson<{ items: { id: string; name: string }[] }>(page, '/api/teams');
    const team = list.items.find((t) => t.name === name);
    expect(team).toBeTruthy();

    // Validation is honest before start.
    const report = await getJson<{ blockers: unknown[]; real_model_configured: boolean }>(
      page, `/api/teams/${team!.id}/validation`,
    );
    expect(report.blockers).toEqual([]);
    expect(report.real_model_configured).toBe(false);

    await page.getByRole('button', { name: '检查并启动团队' }).click();
    await expect(page.getByText(/团队已启动/)).toBeVisible();

    // Every member has its own independent session and 未执行 before a run.
    const snap = await getJson<{
      members: {
        session_id: string; effective_model: string; effective_confidence: string;
        credential_configured: boolean; credential_ref: string;
      }[];
    }>(page, `/api/teams/${team!.id}`);
    const sessions = snap.members.map((m) => m.session_id);
    expect(sessions.length).toBeGreaterThanOrEqual(3);
    expect(new Set(sessions).size).toBe(sessions.length);
    for (const m of snap.members) {
      expect(m.effective_model).toBe('未执行');
      expect(m.effective_confidence).toBe('not_executed');
      expect(m.credential_configured).toBe(true);
      expect(m.credential_ref).toMatch(/^(env:|inprocess:)/);
      expect(m.session_id).toContain(team!.id);
    }

    // Selecting a node opens the inspector with the real inheritance source.
    await memberNode(page, 'implementer').click();
    await expect(inspector(page).getByText('独立会话', { exact: true })).toBeVisible();
    await expect(inspector(page).getByText(/继承来源：/)).toBeVisible();
    await expect(inspector(page).getByText('凭据引用')).toBeVisible();

    // Events are monotonic and attributable.
    const evs = await getJson<{ items: { seq: number; event_type: string }[] }>(
      page, `/api/teams/${team!.id}/events`,
    );
    const seqs = evs.items.map((e) => e.seq);
    expect(seqs).toEqual([...seqs].sort((a, b) => a - b));
    expect(new Set(seqs).size).toBe(seqs.length);
    expect(evs.items.some((e) => e.event_type === 'member.started')).toBe(true);

    // No secret ever reaches the DOM.
    expect(await page.content()).not.toContain('sk-');
  });

  test('暂停 / 恢复是同一位置的状态切换，不新建批次', async ({ page }) => {
    const name = `e2e-暂停-${Date.now().toString(36)}`;
    const team = await seedTeam(page, name);
    await openCanvas(page);
    await selectTeam(page, name);

    await page.getByRole('button', { name: '检查并启动团队' }).click();
    await expect(page.getByText(/团队已启动/)).toBeVisible();
    await memberNode(page, 'implementer').click();

    const implState = async () => {
      const s = await getJson<{ members: { role: string; state: string; run_batch: number }[] }>(
        page, `/api/teams/${team.id}`,
      );
      return s.members.find((m) => m.role === 'implementer')!;
    };

    await page.getByRole('button', { name: '暂停', exact: true }).click();
    await expect.poll(async () => (await implState()).state).toBe('paused');

    await page.getByRole('button', { name: '恢复', exact: true }).click();
    await expect.poll(async () => (await implState()).state).toBe('running');

    // Same member, same batch: a pause is not a restart.
    expect((await implState()).run_batch).toBe(1);
  });

  test('不支持的宿主逐成员模型控件被禁用而非伪装成功', async ({ page }) => {
    const name = `e2e-原生-${Date.now().toString(36)}`;
    const team = await seedTeam(page, name, {
      template_id: null,
      mode: 'product_native',
      members: [
        { role: 'coordinator', agent_host: 'external_a2a' },
        { role: 'worker', agent_host: 'external_a2a' },
      ],
      default_binding: { provider_id: 'local-synthetic', model_id: 'mock-deterministic' },
    });

    // The API refuses the override, with the reason.
    const attempt = await api(
      page, 'PUT', `/api/teams/${team.id}/members/worker/binding`,
      { provider_id: 'local-synthetic', model_id: 'mock-deterministic' },
    );
    expect(attempt.status()).toBe(422);
    const body = await attempt.json();
    expect(body.error.message).toContain('per-member model override');

    // And the UI shows it disabled, with the reason. Members only exist once
    // the team is started, so start it first.
    await openCanvas(page);
    await selectTeam(page, name);
    await page.getByRole('button', { name: '检查并启动团队' }).click();
    await expect(page.getByText(/团队已启动/)).toBeVisible();
    await memberNode(page, 'worker').click();
    await expect(inspector(page).getByLabel(/请求模型（节点覆盖）/)).toBeDisabled();
    await expect(inspector(page).getByLabel(/运行中切换模型/)).toBeDisabled();
    await expect(page.getByText(/逐成员模型覆盖：宿主 external_a2a 不支持/)).toBeVisible();
  });

  test('目标变更后所有受影响成员看到新的计划版本', async ({ page }) => {
    const name = `e2e-目标-${Date.now().toString(36)}`;
    const team = await seedTeam(page, name);
    await openCanvas(page);
    await selectTeam(page, name);
    await page.getByRole('button', { name: '检查并启动团队' }).click();
    await expect(page.getByText(/团队已启动/)).toBeVisible();

    const res = await api(page, 'POST', `/api/teams/${team.id}/goal`, {
      goal: '实现资料脱敏并补齐边界单测',
      reason: 'scope changed',
    });
    expect(res.ok()).toBeTruthy();
    expect((await res.json()).plan_version).toBe(2);

    const snap = await getJson<{ members: { plan_version: number }[] }>(
      page, `/api/teams/${team.id}`,
    );
    for (const m of snap.members) expect(m.plan_version).toBe(2);
  });

  test('请求目录之外的模型被明确拒绝', async ({ page }) => {
    const name = `e2e-非法模型-${Date.now().toString(36)}`;
    const team = await seedTeam(page, name);
    const res = await api(
      page, 'PUT', `/api/teams/${team.id}/members/implementer/binding`,
      { provider_id: 'local-synthetic', model_id: 'gpt-9-imaginary' },
    );
    expect(res.status()).toBe(422);
    expect((await res.json()).error.message).toContain('capability catalog');
  });
});

// ---------------------------------------------------------------- terminal
test.describe('终端刷新重连', () => {
  test('刷新后自动重连到既有会话，而不是回到「启动终端」', async ({ page }) => {
    await loginViaDevToken(page);
    const fs = await import('node:fs/promises');
    const os = await import('node:os');
    const path = await import('node:path');
    const root = await fs.mkdtemp(path.join(os.tmpdir(), 'fy-term-'));
    await fs.writeFile(path.join(root, 'app.py'), 'value = 1\n');

    await page.goto('/workbench');

    // Unique name per run: a workspace carries its own terminal sessions, so a
    // reused name could reattach to a previous run's shell.
    const wsName = `e2e-term-${Date.now().toString(36)}`;

    // Register through the real UI form (the path a user actually takes).
    await page.getByRole('button', { name: '注册工作区' }).click();
    await page.getByLabel('项目名称').fill(wsName);
    await page.getByLabel('授权根目录（绝对路径）').fill(root);
    await page.getByRole('button', { name: '注册', exact: true }).click();
    const chip = page.locator('.workspace-chip', { hasText: wsName });
    await expect(chip).toBeVisible();
    await chip.click();

    await page.getByRole('button', { name: '启动终端' }).click();
    const input = page.getByLabel('终端输入');
    await expect(input).toBeVisible({ timeout: 30_000 });
    await input.fill('echo reattach-probe');
    await input.press('Enter');
    await expect(page.locator('.terminal-output')).toContainText('reattach-probe', {
      timeout: 30_000,
    });

    // Reload: the pane must reattach, not reset to "启动终端".
    await page.reload();
    await expect(page.getByLabel('终端输入')).toBeVisible({ timeout: 30_000 });
    await expect(page.getByText(/已重连到既有会话/)).toBeVisible({ timeout: 30_000 });
    await expect(page.locator('.terminal-output')).toContainText('reattach-probe', {
      timeout: 30_000,
    });

    // And the same session still accepts input after the refresh.
    await page.getByLabel('终端输入').fill('echo after-refresh');
    await page.getByLabel('终端输入').press('Enter');
    await expect(page.locator('.terminal-output')).toContainText('after-refresh', {
      timeout: 30_000,
    });

    // Stop the real PTY: each test spawns a cmd.exe/conhost pair, and leaving
    // them behind exhausts the machine over a full run. The pane keeps the
    // finished session on screen (so its output stays readable) but stops
    // accepting input.
    await page.getByRole('button', { name: '停止' }).click();
    await expect(page.getByLabel('终端输入')).toHaveCount(0);
    await expect(page.getByText(/会话已停止/)).toBeVisible();
    await expect(page.getByRole('button', { name: '停止' })).toHaveCount(0);
  });
});

// ---------------------------------------------------------------- mobile
test.describe('移动端重排', () => {
  test('窄屏保留全部功能入口，不截断', async ({ page }) => {
    await loginViaDevToken(page);
    await page.setViewportSize({ width: 390, height: 844 });
    await page.goto('/workbench');
    await expect(page.getByRole('navigation', { name: '主导航' })).toBeVisible();
    await expect(page.getByRole('button', { name: /工作台空间/ })).toBeVisible();
    await expect(page.getByRole('button', { name: /个人空间/ })).toBeVisible();
    await page.getByRole('link', { name: /协作画布/ }).click();
    await expect(page.getByText('一个入口，组织你的 AI 团队')).toBeVisible();
    // Node inspector stacks below the map instead of disappearing.
    await expect(page.getByRole('complementary', { name: '节点设置' })).toBeVisible();
    // No horizontal overflow.
    const overflow = await page.evaluate(
      () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
    );
    expect(overflow).toBeLessThanOrEqual(2);
  });
});