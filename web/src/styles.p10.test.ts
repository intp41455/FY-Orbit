/**
 * P10 · 平台响应式收口测试。
 *
 * 工单口径：jsdom 不做真实布局，**禁写「像素级不重叠」这类 jsdom 证明不了的
 * 断言**。本文件全部断言 CSS **源文本**里的规则存在且形态正确（三档断点、
 * 目标选择器、域边界），布局正确性由三档截图人工 review 留证（evidence/P10/）。
 */
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, it, expect } from 'vitest';

// vitest 以 web/ 为根运行；jsdom 环境下 import.meta.url 非 file 协议，用 cwd 解析。
const cssPath = resolve(process.cwd(), 'src', 'styles.css');
const css = readFileSync(cssPath, 'utf-8');

/** 截取 P10 段（段头到下一个段头或文件尾）。 */
function p10Section(): string {
  const marker = '/* ---- P10 平台响应式 ----';
  const start = css.indexOf(marker);
  expect(start, 'P10 段必须存在于 styles.css').toBeGreaterThanOrEqual(0);
  const next = css.indexOf('\n/* ---- ', start + marker.length);
  return css.slice(start, next === -1 ? css.length : next);
}

describe('P10 平台响应式（CSS 源文本断言）', () => {
  it('P10 段恰好出现一次且位于文件尾部区域', () => {
    const marker = '/* ---- P10 平台响应式 ----';
    expect(css.split(marker).length - 1).toBe(1);
    expect(css.indexOf(marker)).toBeGreaterThan(css.length * 0.8);
  });

  it('三档断点 1280 / 1024 / 768 各有一个 @media 块', () => {
    const p10 = p10Section();
    expect(p10).toContain('@media (max-width: 1280px)');
    expect(p10).toContain('@media (max-width: 1024px)');
    expect(p10).toContain('@media (max-width: 768px)');
  });

  it('每个 @media 块的花括号配对完整（形态正确）', () => {
    const p10 = p10Section();
    const blocks = p10.match(/@media[^{]+\{(?:[^{}]|\{[^{}]*\})*\}/g) ?? [];
    expect(blocks.length).toBeGreaterThanOrEqual(4);
    for (const block of blocks) {
      const open = (block.match(/\{/g) ?? []).length;
      const close = (block.match(/\}/g) ?? []).length;
      expect(open, block.slice(0, 40)).toBe(close);
    }
  });

  it('768 档把通用两列栅格与工作流布局收为单列（含 minmax(0,1fr) 形态）', () => {
    const block768 = p10Section().match(/@media \(max-width: 768px\)\s*\{[\s\S]*?\n\}/);
    expect(block768, '768 档块必须存在').toBeTruthy();
    const text = block768?.[0] ?? '';
    expect(text).toContain('.grid.cols-2');
    expect(text).toContain('minmax(0, 1fr)');
    expect(text).toContain('.fy-flow');
  });

  it('1024 档：工作台左区占满整行、分隔条隐藏、工作流侧栏转全宽', () => {
    const block1024 = p10Section().match(/@media \(max-width: 1024px\)\s*\{[\s\S]*?\n\}/);
    expect(block1024, '1024 档块必须存在').toBeTruthy();
    const text = block1024?.[0] ?? '';
    expect(text).toContain('.wb-zone-left { flex: 1 1 100%; }');
    expect(text).toContain('.wb-split-handle { display: none; }');
    expect(text).toContain('.fy-flow-side { min-width: 0; max-width: none; flex: 1 1 100%; }');
  });

  it('1280 档提前收窄工作台左区，避免中间档横向滚动', () => {
    const block1280 = p10Section().match(/@media \(max-width: 1280px\)\s*\{[\s\S]*?\n\}/);
    expect(block1280, '1280 档块必须存在').toBeTruthy();
    expect(block1280?.[0]).toContain('.wb-zone-left { min-width: 150px; }');
  });

  it('防溢出工具类 .p10-scroll-x 与 .main pre 横向滚动规则存在', () => {
    const p10 = p10Section();
    expect(p10).toContain('.p10-scroll-x { overflow-x: auto; min-width: 0; }');
    expect(p10).toContain('.main pre { overflow-x: auto; max-width: 100%; }');
  });

  it('域边界：P10 段不包含任何游戏域选择器（cabin/avatar/pet/pixel）', () => {
    const p10 = p10Section().toLowerCase();
    for (const banned of ['.cabin', '.avatar', '.pet', 'pixel-art', '.w2-']) {
      expect(p10, `P10 段不得出现游戏域选择器 ${banned}`).not.toContain(banned);
    }
  });

  it('纪律：P10 段的规则不使用 !important（不破坏他人段落层叠）', () => {
    // 只查段头注释之后的规则体（注释本身包含这条纪律的文字）。
    const withoutHeader = p10Section().replace(/^\s*/g, '').split('*/').slice(1).join('*/');
    expect(withoutHeader).not.toContain('!important');
  });
});
