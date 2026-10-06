// Verifies the new account-deletion entry against the REAL backend.
//
// This ships an irreversible destructive action, so "it compiles" is not
// evidence. This proves, end to end:
//   1. the danger-zone button opens a real .ui-modal dialog
//   2. default focus lands on 取消 (pressing Enter must NOT delete)
//   3. Escape cancels and returns focus to the trigger
//   4. confirming issues DELETE /api/account with CSRF and returns the
//      documented fields
//   5. after deletion the session is genuinely revoked (protected call 401s)
//
// It deletes a throwaway guest account, which is the safest possible target.
//
// Usage: node tools/verify-account-delete.mjs [baseURL]
import { chromium } from 'playwright';

const BASE = process.argv[2] ?? 'http://127.0.0.1:4173';

const browser = await chromium.launch();
const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 } });
const page = await ctx.newPage();

const api = [];
page.on('request', (r) => {
  if (/\/api\/account|\/auth\/guest/.test(r.url())) {
    api.push({ method: r.method(), path: new URL(r.url()).pathname });
  }
});
page.on('response', async (r) => {
  if (/\/api\/account$/.test(new URL(r.url()).pathname)) {
    api[api.length - 1].status = r.status();
    api[api.length - 1].csrfHeaderSent = !!r.request().headers()['x-csrf-token'];
    try { api[api.length - 1].body = await r.json(); } catch { /* non-JSON */ }
  }
});

const checks = [];
const check = (name, pass, detail) => {
  checks.push({ name, pass, detail });
  console.log(`  ${pass ? 'PASS' : 'FAIL'}  ${name}${detail ? `  — ${detail}` : ''}`);
};

// A throwaway guest session; no dev token needed, and it is ours to destroy.
await page.goto(`${BASE}/chat`);
await page.waitForTimeout(1500);
const me = await page.evaluate(async () => {
  const r = await fetch('/auth/me', { credentials: 'include' });
  return r.ok ? await r.json() : null;
});
// /auth/me returns { subject_type, owner_id, is_guest, csrf_token, ... } —
// there is no nested `owner` object, so key off owner_id.
check('拿到游客会话', !!me && !!me.owner_id && me.is_guest === true,
  me ? `owner_id=${me.owner_id.slice(0, 8)}… is_guest=${me.is_guest}` : 'no /auth/me');

await page.goto(`${BASE}/settings`);
await page.waitForTimeout(1500);

// 1. dialog opens
const trigger = page.getByTestId('st-delete-account-open');
check('危险区按钮存在', await trigger.count() === 1);
await trigger.click();
const dlg = page.getByTestId('st-delete-confirm');
await dlg.waitFor({ state: 'visible', timeout: 5000 }).catch(() => {});
check('点击后弹出 .ui-modal 对话框', await dlg.count() === 1 && await dlg.isVisible());

const role = await dlg.getAttribute('role');
const modal = await dlg.getAttribute('aria-modal');
check('对话框语义正确', role === 'dialog' && modal === 'true', `role=${role} aria-modal=${modal}`);

// 2. default focus on 取消
const focusedIsCancel = await page.evaluate(() =>
  document.activeElement?.getAttribute('data-testid') === 'st-delete-cancel');
check('默认焦点落在「取消」（误按 Enter 不会删除）', focusedIsCancel,
  await page.evaluate(() => `activeElement=${document.activeElement?.getAttribute('data-testid') ?? document.activeElement?.tagName}`));

// 3. Escape cancels + focus returns to trigger
await page.keyboard.press('Escape');
await page.waitForTimeout(400);
const closedByEsc = (await page.getByTestId('st-delete-confirm').count()) === 0;
const focusBack = await page.evaluate(() =>
  document.activeElement?.getAttribute('data-testid') === 'st-delete-account-open');
check('Esc 关闭对话框', closedByEsc);
check('关闭后焦点还给触发按钮（R6 回焦）', focusBack);

// 4. confirm actually deletes
await trigger.click();
await page.getByTestId('st-delete-confirm').waitFor({ state: 'visible', timeout: 5000 });
await page.getByTestId('st-delete-confirm-btn').click();
await page.waitForTimeout(2500);

const del = api.find((r) => r.method === 'DELETE');
check('确认后发出 DELETE /api/account', !!del, del ? `status=${del.status}` : '未发出');
check('DELETE 带上了 CSRF 头', !!del?.csrfHeaderSent);
const b = del?.body ?? {};
check('返回体字段符合后端契约', b.status === 'deleted'
  && typeof b.memories_deleted === 'number'
  && typeof b.sessions_revoked === 'number'
  && typeof b.consents_retained === 'number',
  JSON.stringify({ status: b.status, memories: b.memories_deleted, sessions: b.sessions_revoked, consents: b.consents_retained }));

// 5. session really revoked
const after = await page.evaluate(async () => {
  const r = await fetch('/api/account/consents', { credentials: 'include' });
  return r.status;
});
check('删除后会话已失效（受保护端点不再 200）', after !== 200, `GET /api/account/consents -> ${after}`);

await browser.close();

const failed = checks.filter((c) => !c.pass);
console.log(`\n${checks.length - failed.length}/${checks.length} 项通过`);
console.log('请求轨迹:', JSON.stringify(api));
process.exit(failed.length ? 1 : 0);
