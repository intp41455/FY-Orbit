import { test, expect, type Page } from '@playwright/test';
import fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';

/**
 * P1-13 侧边定位（大纲/搜索 → 编辑区跳转）— E2E against the REAL backend.
 *
 * Evidence (screenshots + DOM assertions):
 *  - 多级标题被解析为大纲，层级缩进正确；
 *  - 点击大纲项 → textarea 滚动到目标行（scrollTop > 0）、
 *    data-last-jump == 目标行号、selectionStart == 目标行首偏移；
 *  - 当前文件内搜索列出全部命中行，点击命中 → 跳转并选中关键词；
 *  - 截图留档。
 */

const LOCAL_TOKEN = process.env.E2E_LOCAL_TOKEN ?? '';
const SHOT_DIR = path.join('..', 'evidence', 'p1-outline-20261003');

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
  if (!res.ok()) throw new Error(`dev-token login failed: HTTP ${res.status()} — set E2E_LOCAL_TOKEN`);
  const session = extractCookie(res.headers()['set-cookie'], 'fy_session');
  if (session) {
    await page.context().addCookies([
      { name: 'fy_session', value: session, domain: '127.0.0.1', path: '/', httpOnly: true, sameSite: 'Lax' },
    ]);
  }
  csrfToken = (await res.json()).csrf_token;
}

async function api(page: Page, method: 'GET' | 'POST', pathName: string, body?: unknown) {
  return page.request.fetch(pathName, {
    method,
    data: body === undefined ? undefined : JSON.stringify(body),
    headers: {
      'Content-Type': 'application/json',
      ...(csrfToken ? { 'X-CSRF-Token': csrfToken } : {}),
    },
  });
}

// ---- fixture：一个带多级标题与重复关键词的真实 md 文件 ----
const FILE_NAME = 'outline-guide.md';
const TARGET_HEADING = '部署说明';
const KEYWORD = 'verify-p113';

/** 程序化生成 md 内容，返回 { content, deployLine, keywordLines }。 */
function buildGuideMarkdown(): { content: string; deployLine: number; keywordLines: number[] } {
  const lines: string[] = [];
  const keywordLines: number[] = [];
  lines.push('# 使用指南');
  lines.push('');
  lines.push('## 快速开始');
  lines.push('');
  for (let i = 1; i <= 8; i++) lines.push(`快速开始第 ${i} 段说明文字，用于撑起编辑器滚动高度。`);
  lines.push('### 环境要求');
  lines.push('');
  for (let i = 1; i <= 6; i++) lines.push(`环境要求第 ${i} 段：Node.js 20+，npm 10+。`);
  const deployLine = lines.length + 1;
  lines.push(`## ${TARGET_HEADING}`);
  lines.push('');
  for (let i = 1; i <= 6; i++) lines.push(`部署说明第 ${i} 段：构建产物需 ${KEYWORD} 校验通过。`);
  lines.push('### 回滚步骤');
  lines.push('');
  for (let i = 1; i <= 6; i++) lines.push(`回滚第 ${i} 段：执行 ${KEYWORD} 脚本并核对哈希。`);
  lines.push('## 附录');
  lines.push('');
  lines.push(`附录：${KEYWORD} 是本次验收关键词。`);
  lines.push('');
  for (let i = 1; i <= 10; i++) lines.push(`附录第 ${i} 段收尾文字。`);
  const content = lines.join('\n');
  lines.forEach((l, i) => {
    if (l.includes(KEYWORD)) keywordLines.push(i + 1);
  });
  return { content, deployLine, keywordLines };
}

let GUIDE: ReturnType<typeof buildGuideMarkdown>;

async function makeFixtureRoot(): Promise<string> {
  GUIDE = buildGuideMarkdown();
  const root = await fs.mkdtemp(path.join(os.tmpdir(), 'fy-p113-'));
  await fs.writeFile(path.join(root, FILE_NAME), GUIDE.content, 'utf8');
  await fs.writeFile(path.join(root, 'README.md'), '# 示例项目\n\n仅用于撑起文件树。', 'utf8');
  return root;
}

function lineStartOffset(content: string, line1based: number): number {
  const lines = content.split('\n');
  let off = 0;
  for (let i = 0; i < Math.min(line1based - 1, lines.length); i++) off += lines[i].length + 1;
  return off;
}

async function openWorkspace(page: Page, root: string, name: string): Promise<void> {
  await page.goto('/workbench');
  await page.getByRole('button', { name: '注册工作区' }).click();
  await page.getByLabel('项目名称').fill(name);
  await page.getByLabel('授权根目录（绝对路径）').fill(root);
  await page.getByRole('button', { name: '注册', exact: true }).click();
  const chip = page.locator('.workspace-chip', { hasText: name });
  await expect(chip).toBeVisible();
  await chip.click();
  await expect(page.locator('.file-tree').getByText(FILE_NAME)).toBeVisible();
}

interface EditorState {
  scrollTop: number;
  scrollHeight: number;
  clientHeight: number;
  selectionStart: number;
  selectionEnd: number;
  lastJump: string | null;
}

async function readEditorState(page: Page): Promise<EditorState> {
  return page.evaluate(() => {
    const ta = document.querySelector<HTMLTextAreaElement>('[data-testid="code-area"]');
    if (!ta) throw new Error('code-area textarea not found');
    return {
      scrollTop: ta.scrollTop,
      scrollHeight: ta.scrollHeight,
      clientHeight: ta.clientHeight,
      selectionStart: ta.selectionStart,
      selectionEnd: ta.selectionEnd,
      lastJump: ta.getAttribute('data-last-jump'),
    };
  });
}

test.describe('P1-13 侧边定位（真实后端）', () => {
  let root: string;

  test.beforeAll(async () => {
    root = await makeFixtureRoot();
  });

  test('点击大纲项滚动至目标行并高亮选中', async ({ page }) => {
    await loginViaDevToken(page);
    await openWorkspace(page, root, `p1-outline-${Date.now().toString(36)}`);

    await page.locator('.file-tree').getByText(FILE_NAME).click();
    const ta = page.getByTestId('code-area');
    await expect(ta).toBeVisible();
    await expect(ta).toHaveValue(GUIDE.content);

    // 大纲面板渲染了全部标题
    const panel = page.getByTestId('outline-panel');
    await expect(panel).toBeVisible();
    const items = page.getByTestId('outline-item');
    await expect(items).toHaveCount(6); // #使用指南 ##快速开始 ###环境要求 ##部署说明 ###回滚步骤 ##附录
    // 层级缩进：一级 6px、二级 18px、三级 30px
    await expect(items.filter({ hasText: '使用指南' })).toHaveAttribute('style', /padding-left:\s*6px/);
    await expect(items.filter({ hasText: TARGET_HEADING })).toHaveAttribute('style', /padding-left:\s*18px/);
    await expect(items.filter({ hasText: '回滚步骤' })).toHaveAttribute('style', /padding-left:\s*30px/);

    // 初始未滚动
    const before = await readEditorState(page);
    expect(before.lastJump).toBeNull();

    // 点击二级标题 “## 部署说明” → 滚动 + 选中标题文本 + data-last-jump
    await items.filter({ hasText: TARGET_HEADING }).click();
    const headingLine = GUIDE.content.split('\n')[GUIDE.deployLine - 1];
    const headingCol = headingLine.indexOf(TARGET_HEADING);
    const expectedOffset = lineStartOffset(GUIDE.content, GUIDE.deployLine) + headingCol;
    await expect(ta).toHaveAttribute('data-last-jump', String(GUIDE.deployLine));
    const after = await readEditorState(page);
    expect(after.scrollTop).toBeGreaterThan(0);
    expect(after.selectionStart).toBe(expectedOffset);
    expect(after.selectionEnd).toBe(expectedOffset + TARGET_HEADING.length);
    await page.screenshot({ path: path.join(SHOT_DIR, 'p1-13-outline-jump.png'), fullPage: true });

    // 再点击一级标题回到顶部（行 1 → scrollTop 归 0）
    await items.filter({ hasText: '使用指南' }).click();
    await expect(ta).toHaveAttribute('data-last-jump', '1');
    const back = await readEditorState(page);
    expect(back.scrollTop).toBe(0);
  });

  test('当前文件内搜索：全部命中列出，点击命中跳转并选中关键词', async ({ page }) => {
    await loginViaDevToken(page);
    await openWorkspace(page, root, `p1-search-${Date.now().toString(36)}`);

    await page.locator('.file-tree').getByText(FILE_NAME).click();
    const ta = page.getByTestId('code-area');
    await expect(ta).toBeVisible();

    await page.getByTestId('outline-search').fill(KEYWORD);
    const hits = page.getByTestId('outline-hit');
    await expect(hits).toHaveCount(GUIDE.keywordLines.length);

    // 命中行号与关键词高亮（<mark>）
    const firstLine = GUIDE.keywordLines[0];
    await expect(hits.first()).toHaveAttribute('data-line', String(firstLine));
    await expect(hits.first().getByTestId('hit-mark')).toHaveText(KEYWORD);

    // 点击首个命中 → 跳到该行并选中关键词文本
    await hits.first().click();
    const lineText = GUIDE.content.split('\n')[firstLine - 1];
    const col = lineText.indexOf(KEYWORD);
    const expectedSel = lineStartOffset(GUIDE.content, firstLine) + col;
    await expect(ta).toHaveAttribute('data-last-jump', String(firstLine));
    const st = await readEditorState(page);
    expect(st.selectionStart).toBe(expectedSel);
    expect(st.selectionEnd).toBe(expectedSel + KEYWORD.length);
    await page.screenshot({ path: path.join(SHOT_DIR, 'p1-13-search-jump.png'), fullPage: true });
  });
});
