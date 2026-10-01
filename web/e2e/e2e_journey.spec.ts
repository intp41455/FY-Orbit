import { test, expect } from '@playwright/test';
import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

// Output directory for step-by-step evidence screenshots
const EVIDENCE_DIR = path.resolve(__dirname, '../../evidence/e2e-screenshots');

test.beforeAll(() => {
  if (!fs.existsSync(EVIDENCE_DIR)) {
    fs.mkdirSync(EVIDENCE_DIR, { recursive: true });
  }
});

test.describe('End-to-End User Journey (Step 1 Local Verification)', () => {
  test('Complete 11-step local journey: Login, Profile, Feedback, Canvas, and Dual-Agent dispatch', async ({ page }) => {
    test.setTimeout(300_000);
    page.on('console', (msg) => console.log(`[Browser Console] ${msg.type()}: ${msg.text()}`));
    page.on('pageerror', (err) => console.log(`[Browser PageError] ${err}`));

    // Set a wide viewport for desktop demonstration
    await page.setViewportSize({ width: 1280, height: 900 });

    // -------------------------------------------------------------------------
    // Step 1: Open Login Page (01_login_page.png)
    // -------------------------------------------------------------------------
    await page.goto('/login');
    await expect(page.getByRole('heading', { name: 'Find Yourself' })).toBeVisible();
    await expect(page.getByRole('button', { name: /本地口令直接登录/ })).toBeVisible();
    await page.screenshot({ path: path.join(EVIDENCE_DIR, '01_login_page.png'), fullPage: true });

    // -------------------------------------------------------------------------
    // Step 2: Local Dev Token Login -> Authenticated Shell (02_authenticated_shell.png)
    // -------------------------------------------------------------------------
    const tokenInput = page.locator('input[type="password"]');
    await tokenInput.fill('dev-token-secret');
    await page.getByRole('button', { name: /本地口令直接登录/ }).click();
    await expect(page).toHaveURL(/\/chat/);
    await expect(page.getByRole('link', { name: '多维画像' })).toBeVisible();
    await expect(page.getByRole('link', { name: '协作画布' })).toBeVisible();
    await page.screenshot({ path: path.join(EVIDENCE_DIR, '02_authenticated_shell.png'), fullPage: true });

    // -------------------------------------------------------------------------
    // Step 3: Navigate to Profiles & Create New Subject (03_subject_created.png)
    // -------------------------------------------------------------------------
    await page.goto('/profiles');
    await expect(page.getByRole('heading', { name: /个人与对象多维画像/ })).toBeVisible();

    // Click "+ 新建档案对象"
    await page.getByRole('button', { name: '+ 新建档案对象' }).click();
    await page.locator('#new-label').fill('李明 (产品架构师)');
    await page.locator('#new-kind').selectOption('person');
    await page.locator('#new-desc').fill('自我探索与跨智能体协作能力画像评估');
    await page.getByRole('button', { name: '确认创建' }).click();

    // Wait for the subject tab to appear and be active
    await expect(page.getByRole('button', { name: /李明 \(产品架构师\)/ })).toBeVisible();
    await page.screenshot({ path: path.join(EVIDENCE_DIR, '03_subject_created.png'), fullPage: true });

    // -------------------------------------------------------------------------
    // Step 4: Import Multi-Speaker Dialogue Corpus (04_document_imported.png)
    // -------------------------------------------------------------------------
    const corpusText = `李明: 我们在推进协作引擎时，我发现团队最大的阻力在于跨智能体的上下文丢失。我希望能建立统一的状态交接包协议。
王工: 技术上我们可以通过轻量级的 durable outbox 来保证派发任务不丢失，但你需要明确跨域授权机制。
李明: 没错，所有私人日记和未授权记录严格按需切片，绝不能全量透传给第三方执行代理。我们需要强类型和零模拟。
王工: 赞同，这样既保证可追溯，又能防止敏感数据污染。`;

    await page.locator('#doc-content').fill(corpusText);
    await page.locator('#doc-filename').fill('collaboration_architecture.txt');
    await page.getByRole('button', { name: '提交语料切片' }).click();

    // Wait for slice import analysis to show
    await expect(page.getByText('最近导入切片分析')).toBeVisible();
    await expect(page.getByText('检测到的发言人切片归属映射：')).toBeVisible();
    await page.screenshot({ path: path.join(EVIDENCE_DIR, '04_document_imported.png'), fullPage: true });

    // -------------------------------------------------------------------------
    // Step 5: Confirm Speakers Attribution (05_speakers_confirmed.png)
    // -------------------------------------------------------------------------
    const selects = page.locator('.card select').filter({ hasText: /映射为本人/ });
    const count = await selects.count();
    if (count > 0) {
      await selects.first().selectOption('self');
    }
    await page.getByRole('button', { name: '确认发言人归属' }).click();
    await expect(page.getByText(/已确认.*个发言人切片归属/)).toBeVisible();
    await page.screenshot({ path: path.join(EVIDENCE_DIR, '05_speakers_confirmed.png'), fullPage: true });

    // -------------------------------------------------------------------------
    // Step 6: Trigger Profile Derivation & View Graph (06_profile_revision_graph.png)
    // -------------------------------------------------------------------------
    await page.getByRole('button', { name: '启动画像综合推演 (Run)' }).click();
    // Wait for version / clusters to render
    await expect(page.getByText('客观语料统计指标说明')).toBeVisible({ timeout: 25000 });
    await expect(page.getByText(/客观量化推演维度/)).toBeVisible();
    await page.screenshot({ path: path.join(EVIDENCE_DIR, '06_profile_revision_graph.png'), fullPage: true });

    // -------------------------------------------------------------------------
    // Step 7: Submit Correction/Negative Feedback on Node (07_profile_feedback_corrected.png)
    // -------------------------------------------------------------------------
    // Switch to detailed list view to inspect nodes and click "否定"
    await page.getByRole('button', { name: '详细列表' }).click();
    await expect(page.getByRole('button', { name: '否定' }).first()).toBeVisible();
    await page.getByRole('button', { name: '否定' }).first().click();
    await page.waitForTimeout(600);
    await page.screenshot({ path: path.join(EVIDENCE_DIR, '07_profile_feedback_corrected.png'), fullPage: true });

    // -------------------------------------------------------------------------
    // Step 8 & 9: Create Canvas Instance & View Probes (08 & 09)
    // -------------------------------------------------------------------------
    await page.goto('/canvas');
    await expect(page.getByRole('heading', { name: /多.*Agent.*协作/ })).toBeVisible();

    await page.getByRole('button', { name: '+ 新建画布项目' }).click();
    await page.locator('#new-inst-name').fill('产品架构多智能体推演');
    await page.locator('#new-inst-tmpl').selectOption('personal');
    await page.getByRole('button', { name: '立即创建' }).click();

    await expect(page.getByRole('button', { name: /产品架构多智能体推演/ })).toBeVisible();
    await expect(page.getByText(/主控 Agent:/)).toBeVisible();

    // Verify machine connectors & probe ladder
    const probeCard = page.locator('.card', { hasText: '实机连接探针与状态阶梯' });
    await expect(probeCard).toBeVisible();
    await expect(probeCard.getByText('Hermes', { exact: true })).toBeVisible();
    await expect(probeCard.getByText('ResearchAgent', { exact: true })).toBeVisible();

    // Capture connectors view
    await probeCard.scrollIntoViewIfNeeded();
    await page.screenshot({ path: path.join(EVIDENCE_DIR, '08_canvas_connectors.png'), fullPage: true });

    // Capture instance topology card
    await page.getByText(/协作拓扑:/).scrollIntoViewIfNeeded();
    await page.screenshot({ path: path.join(EVIDENCE_DIR, '09_canvas_instance_created.png'), fullPage: true });

    // -------------------------------------------------------------------------
    // Step 10: Dispatch Subtask 1 to Hermes (10_canvas_subtask_completed.png)
    // -------------------------------------------------------------------------
    await page.getByRole('button', { name: '派发子任务' }).click();
    await page.locator('#dispatch-worker-select').selectOption('Hermes');
    await page.locator('#dispatch-goal-input').fill('echo: 确认协作画布与数据域授权协议状态');
    await page.locator('#dispatch-budget-input').fill('0.20');
    await page.getByRole('button', { name: '确认派发' }).click();
    await expect(page.getByRole('button', { name: '派发执行中...' })).toBeVisible();

    // Wait for subtask execution completion or entry in list
    await expect(page.getByText('echo: 确认协作画布与数据域授权协议状态', { exact: true })).toBeVisible({ timeout: 180000 });
    await page.screenshot({ path: path.join(EVIDENCE_DIR, '10_canvas_subtask_completed.png'), fullPage: true });

    // -------------------------------------------------------------------------
    // Step 11: View Events Timeline & Handoff (11_canvas_events_timeline.png)
    // -------------------------------------------------------------------------
    // Scroll down to view the Event Timeline and snapshot
    const timelineCard = page.locator('.card', { hasText: '事件时间线' });
    await timelineCard.scrollIntoViewIfNeeded();
    await expect(timelineCard).toBeVisible();
    await page.screenshot({ path: path.join(EVIDENCE_DIR, '11_canvas_events_timeline.png'), fullPage: true });
  });
});
