import { mkdirSync, writeFileSync } from 'node:fs';
import path from 'node:path';
import { test, expect, type Page } from '@playwright/test';

/**
 * P1-16 trace 前端视图 E2E — 真实运行证据。
 *
 * 运行方式（dev server 独立端口 5195，不占用他人端口）：
 *   npx vite --port 5195 --strictPort --host 127.0.0.1
 *   E2E_BASE_URL=http://127.0.0.1:5195 E2E_PREVIEW_PORT=5195 \
 *     npx playwright test e2e/p1-trace-view.spec.ts --project=desktop \
 *     --output=../evidence/p1-16/test-results
 *
 * 产物（全部落 evidence/p1-16/）：
 * - 三层样例 + 完整集 + 详情浮层 + canvas 真实 trace + 非法 trace 截图
 * - schema-validation-log.json（页面内逐字段校验日志，字段名/类型/缺失项）
 */

const EVIDENCE_DIR = path.resolve(process.cwd(), '..', 'evidence', 'p1-16');

async function expectValidated(page: Page) {
  await expect(page.getByTestId('trace-validation-summary')).toBeVisible();
}

test.describe('P1-16 trace 时序视图', () => {
  test.beforeAll(() => {
    mkdirSync(EVIDENCE_DIR, { recursive: true });
  });

  test.beforeEach(async ({ page }) => {
    await page.goto('/trace.html');
    await expect(page.getByTestId('trace-view')).toBeVisible();
  });

  test('三层样例（L1/L2/L3）各渲染一例且 schema 校验通过', async ({ page }) => {
    for (const [key, layer] of [
      ['sample-l1', 'L1'],
      ['sample-l2', 'L2'],
      ['sample-l3', 'L3'],
    ] as const) {
      await page.getByTestId(key).click();
      await expectValidated(page);
      await expect(page.getByTestId('trace-validation-summary')).toContainText('✓ schema 校验通过');
      await expect(page.getByTestId('trace-validation-summary')).toContainText('失败 0');
      await expect(page.getByTestId('trace-node-1')).toBeVisible();
      await page.screenshot({ path: path.join(EVIDENCE_DIR, `trace-${layer}.png`), fullPage: true });
    }
  });

  test('完整三层样例集渲染 3 节点，校验日志落 evidence/', async ({ page }) => {
    await page.getByTestId('sample-full').click();
    await expectValidated(page);
    await expect(page.getByTestId('trace-node-1')).toBeVisible();
    await expect(page.getByTestId('trace-node-2')).toBeVisible();
    await expect(page.getByTestId('trace-node-3')).toBeVisible();
    const lanes = await page.getByTestId('trace-lane').allTextContents();
    expect(lanes).toContain('owner-local');
    expect(lanes).toContain('Hermes');
    expect(lanes).toContain('L2 记忆层');

    const log = await page.evaluate(() => (window as unknown as Record<string, never>).__TRACE_VALIDATION_LOG__);
    expect(log).toBeTruthy();
    expect(log.summary.fail).toBe(0);
    expect(log.eventCount).toBe(3);
    writeFileSync(path.join(EVIDENCE_DIR, 'schema-validation-log.json'), JSON.stringify(log, null, 2));

    await page.screenshot({ path: path.join(EVIDENCE_DIR, 'trace-full-set.png'), fullPage: true });
  });

  test('点击节点打开详情浮层展示完整字段（L2 写回事件）', async ({ page }) => {
    await page.getByTestId('sample-full').click();
    await expect(page.getByTestId('trace-node-3')).toBeVisible();
    await page.getByTestId('trace-node-3').click();
    const detail = page.getByTestId('trace-detail');
    await expect(detail).toBeVisible();
    await expect(detail).toContainText('memory.upsert');
    await expect(detail).toContainText('plaintext_egress');
    await expect(detail).toContainText('aes-256-gcm');
    await page.screenshot({ path: path.join(EVIDENCE_DIR, 'trace-detail-overlay.png'), fullPage: true });
  });

  test('真实 Canvas→Hermes 往返 trace 渲染 10 节点', async ({ page }) => {
    await page.getByTestId('sample-canvas').click();
    await expectValidated(page);
    for (let i = 1; i <= 10; i++) {
      await expect(page.getByTestId(`trace-node-${i}`)).toBeAttached();
    }
    const lanes = await page.getByTestId('trace-lane').allTextContents();
    expect(lanes).toContain('canvas');
    expect(lanes).toContain('Hermes');
    await page.setViewportSize({ width: 2480, height: 920 });
    await page.screenshot({ path: path.join(EVIDENCE_DIR, 'trace-canvas-roundtrip.png'), fullPage: true });
  });

  test('上传非法 trace：文件上传通道可用且校验失败显著告警', async ({ page }) => {
    const doc = {
      trace_version: '1.0',
      trace_id: '783de07618ef21e12bb25bfce84f665b',
      seq: 1,
      ts: '2026-10-03T00:00:00+00:00',
      layer: 'L2',
      actor: { actor_id: 'owner-local', actor_kind: 'owner', conversation_id: null, task_id: null },
      query_target: { kind: 'memory', record_id: 'mem-x', record_kind: 'memory', domain: 'shared', locator: null },
      query_method: {
        mode: 'vector',
        index_tier: 'hot_local',
        cache_hit: false,
        online: true,
        authorization: { predicate: 'grants.is_authorized', grant_id: null, consumer_domain: 'shared' },
      },
      hits: [],
      agent: { agent_id: 'Hermes', role: 'worker', agent_version: null, model: null, provider: null },
      writeback: { action: 'none', target_ids: [] },
      // 负例：明文外传开关被打开 —— schema 硬约束 plaintext_egress 恒 false
      egress: {
        authorized: false,
        grant_id: null,
        destination: 'local',
        cipher: 'none',
        key_id: 'k',
        key_escrow: 'local_only',
        plaintext_egress: true,
        audit_seq: 1,
      },
      redaction: { blacklist_version: null, matched: false, redacted_fields: [] },
      integrity: {
        prev_trace_hash: '0'.repeat(64),
        audit_chain_verified: true,
      },
    };
    await page.setInputFiles('[data-testid="trace-file-input"]', {
      name: 'invalid-plaintext-egress.json',
      mimeType: 'application/json',
      buffer: Buffer.from(JSON.stringify(doc, null, 2)),
    });
    await expectValidated(page);
    await expect(page.getByTestId('trace-validation-summary')).toContainText('✗ schema 校验失败');
    const summary = await page.getByTestId('trace-validation-summary').textContent();
    expect(summary).toMatch(/失败 [1-9]\d*/);
    // 明细默认只显示前 40 条，展开全部后再断言失败字段可见
    await page.getByRole('button', { name: /展开全部/ }).click();
    await expect(page.getByTestId('trace-validation-issues')).toContainText('plaintext_egress');
    await page.screenshot({ path: path.join(EVIDENCE_DIR, 'trace-invalid-rejected.png'), fullPage: true });
  });
});
