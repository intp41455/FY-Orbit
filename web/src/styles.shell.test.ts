/**
 * 应用外壳 · 浏览器缩放回归护栏（130% 缩放布局崩坏）。
 *
 * jsdom 不做真实布局，无法断言像素级「不重叠/不裁切」。本测试改为解析样式源文本，
 * 锁定两条**机制性事实**，防止修复被回退：
 *   1. 侧栏的横向裁剪只对「竖排侧栏」成立。≤860px 时 tokens.css 把侧栏改造成
 *      靠 overflow-x:auto 横向滚动的顶部抽屉；本层在 tokens.css 之后加载，
 *      无条件写 overflow-x:hidden 会把滚动条顶掉、让导航无声消失。
 *   2. min-width:0 只补在竖排侧栏。滚动容器里给条目 min-width:0 会让不可断的
 *      导航条目缩到比文字还窄，文字溢出盒子互相叠字——比裁切更糟。
 *
 * 真实像素结论见交付报告（130% 等效视口实测：侧栏 scrollWidth 2097、
 * overflow-x 由 hidden 恢复为 auto、可横向滚动）。
 */
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, it, expect } from 'vitest';

// vitest 以 web/ 为根运行（与 styles.p10.test.ts 同一口径）。
const shell = readFileSync(resolve(process.cwd(), 'src', 'styles', 'desktop-window.css'), 'utf-8');
const tokens = readFileSync(resolve(process.cwd(), 'src', 'styles', 'tokens.css'), 'utf-8');

/** 抽出所有 `@media (min-width: 861px) { ... }` 的规则体并拼接（配平花括号）。 */
function verticalTier(css: string): string {
  const parts: string[] = [];
  const re = /@media\s*\(min-width:\s*861px\)\s*\{/g;
  let m: RegExpExecArray | null;
  while ((m = re.exec(css)) !== null) {
    let depth = 0;
    let i = m.index + m[0].length - 1;
    const start = i + 1;
    for (; i < css.length; i++) {
      if (css[i] === '{') depth++;
      else if (css[i] === '}') {
        depth--;
        if (depth === 0) { parts.push(css.slice(start, i)); re.lastIndex = i; break; }
      }
    }
  }
  return parts.join('\n');
}

/** 去掉所有 @media 块，只剩「无条件生效」的顶层规则。 */
function topLevelOnly(css: string): string {
  return css.replace(/@media[^{]+\{(?:[^{}]|\{[^{}]*\})*\}/g, '');
}

describe('外壳 · 侧栏横向裁剪只限竖排档', () => {
  it('无条件规则里不再有 .sidebar { overflow-x: hidden }', () => {
    expect(topLevelOnly(shell)).not.toMatch(/\.sidebar\s*\{[^}]*overflow-x:\s*hidden/);
  });

  it('横向裁剪被收进 @media (min-width: 861px)', () => {
    const tier = verticalTier(shell);
    expect(tier, '必须存在 min-width:861px 的竖排侧栏档').not.toBe('');
    expect(tier).toMatch(/\.sidebar\s*\{[^}]*overflow-x:\s*hidden/);
  });

  it('≤860px 顶部抽屉档仍保留 overflow-x: auto（滚动能力不被顶掉）', () => {
    const m = tokens.match(/@media\s*\(max-width:\s*860px\)\s*\{/);
    expect(m, 'tokens.css 必须仍有 ≤860px 侧栏档').not.toBeNull();
    const block = tokens.slice(m!.index!);
    expect(block).toMatch(/\.sidebar\s*\{[\s\S]*?overflow-x:\s*auto/);
    expect(block).toMatch(/\.nav\s*\{[\s\S]*?flex-direction:\s*row/);
  });
});

describe('外壳 · flex/grid 子项 min-width: 0', () => {
  it('.app 的栅格子项无条件可收缩', () => {
    expect(topLevelOnly(shell)).toMatch(/\.app\s*>\s*\*\s*\{[^}]*min-width:\s*0/);
  });

  it('竖排档里 .sidebar 及其子项可收缩（与 G4 给 .main 的口径一致）', () => {
    const tier = verticalTier(shell);
    expect(tier).toMatch(/\.sidebar\s*,\s*\.sidebar\s*>\s*\*\s*,/);
    expect(tier).toMatch(/\.sidebar \.nav a\s*,\s*\.sidebar \.nav a\s*>\s*span\s*,/);
    expect(tier).toMatch(/\.sidebar \.sidebar-foot\s*,/);
    expect(tier).toMatch(/min-width:\s*0/);
  });

  it('min-width:0 不外溢到顶部抽屉档（否则 nowrap 导航条目会互相叠字）', () => {
    // 顶层只剩 .app > *；任何 .nav a 的 min-width:0 都必须待在竖排档里。
    expect(topLevelOnly(shell)).not.toMatch(/\.nav a\s*(?:,\s*[^;{]*)?\{[^}]*min-width:\s*0/);
    expect(verticalTier(shell)).toMatch(/\.sidebar \.nav a\s*(?:,[^{]*)?\{[^}]*min-width:\s*0/);
  });
});