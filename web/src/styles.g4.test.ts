/**
 * G4 · 响应式 / UI 错位修复回归护栏（D 组）。
 *
 * jsdom 不做真实布局，无法断言像素级不重叠；本测试改为解析 styles.css 源文本，
 * 锁定 G4 追加段的关键规则确实存在且形态正确，防止修复被回退或再次漂移。
 * 真实三档（1280/1024/768）视觉截图由主控起服务后用 Playwright 收尾（见报告）。
 */
// 用 node:fs 真实读取同目录 styles.css 源文本：vitest 运行期由 esbuild 直接执行，
// 类型由 @types/node 提供（已在 tsconfig.types 中登记）。
import fs from 'node:fs';
import { fileURLToPath } from 'node:url';
import path from 'node:path';

const here = path.dirname(fileURLToPath(import.meta.url));
const css = fs.readFileSync(path.join(here, 'styles.css'), 'utf8');
import { describe, it, expect } from 'vitest';

// G4 段：从段落标记到文件结尾。
const g4Start = css.indexOf('---- G4 UI 错位修复 ----');
const g4 = g4Start >= 0 ? css.slice(g4Start) : '';

describe('G4 section present', () => {
  it('appends an owned G4 section', () => {
    expect(g4Start).toBeGreaterThan(0);
  });
});

describe('G4-8 spacing/size tokens', () => {
  it('defines --space-1..8 and sidebar size tokens', () => {
    for (let i = 1; i <= 8; i++) {
      expect(g4).toContain(`--space-${i}:`);
    }
    expect(g4).toContain('--size-sidebar-narrow:');
  });
});

describe('G4 main responsive breakpoint', () => {
  it('narrows the .app sidebar at <=1100px (the missing middle tier)', () => {
    const m = g4.match(/@media\s*\(max-width:\s*1100px\)\s*\{([\s\S]*?)\n\}/);
    expect(m).not.toBeNull();
    expect(m![1]).toMatch(/\.app\s*\{[^}]*--size-sidebar-narrow/);
  });
  it('gives main-area children min-width:0', () => {
    expect(g4).toMatch(/\.main\s*>\s*\*[^}]*min-width:\s*0/);
  });
});

describe('G4-1 decorate/back no overlap', () => {
  it('moves .w2-toggle off the back button position', () => {
    const rule = g4.match(/\.w2-toggle\s*\{([^}]*)\}/);
    expect(rule).not.toBeNull();
    expect(rule![1]).not.toMatch(/left:\s*1rem/);
    expect(rule![1]).toMatch(/left:\s*5\.25rem/);
  });
});

describe('G4-2 toolbars scrollable on short viewports', () => {
  it('gives .cabin-toolbar max-height + overflow', () => {
    const rule = g4.match(/\.cabin-toolbar\s*\{([\s\S]*?)\}/)!;
    expect(rule[1]).toMatch(/max-height/);
    expect(rule[1]).toMatch(/overflow-y:\s*auto/);
  });
  it('gives .cabin-indoor-bar max-height + overflow and aligns top to 14px', () => {
    const rule = g4.match(/\.cabin-indoor-bar\s*\{([\s\S]*?)\}/)!;
    expect(rule[1]).toMatch(/max-height/);
    expect(rule[1]).toMatch(/overflow-y:\s*auto/);
    expect(rule[1]).toMatch(/--hud-top/);
  });
});

describe('G4-3 workshop continuous layout', () => {
  it('adds a two-column middle tier for 1025-1400px', () => {
    expect(g4).toMatch(/@media\s*\(min-width:\s*1025px\)\s*and\s*\(max-width:\s*1400px\)/);
  });
  it('collapses to one column at <=1024px', () => {
    expect(g4).toMatch(/@media\s*\(max-width:\s*1024px\)[\s\S]*?grid-template-columns:\s*minmax\(0,\s*1fr\)/);
  });
});

describe('G4-4 tuning grid columns', () => {
  it('uses max-content + minmax(0,1fr) on .avatar-field', () => {
    const rule = g4.match(/\.avatar-field\s*\{([^}]*)\}/)!;
    expect(rule[1]).toMatch(/max-content\s+minmax\(0,\s*1fr\)/);
  });
  it('uses minmax columns on .avatar-two-col', () => {
    const rule = g4.match(/\.avatar-two-col\s*\{([^}]*)\}/)!;
    expect(rule[1]).toMatch(/minmax\(0,\s*1fr\)\s+minmax\(0,\s*1fr\)/);
  });
});

describe('G4-5 flexible indoor input', () => {
  it('makes .cabin-input flexible instead of fixed 9.5rem', () => {
    const rule = g4.match(/\.cabin-input\s*\{([\s\S]*?)\}/)!;
    expect(rule[1]).toMatch(/flex:\s*1\s+1\s+8rem/);
    expect(rule[1]).not.toMatch(/width:\s*9\.5rem/);
  });
});

describe('G4-6 glow overflow guard', () => {
  it('caps the decorative glow and clips horizontal overflow', () => {
    expect(g4).toMatch(/\.app::before\s*\{[^}]*max-width:\s*60vmin/);
    expect(g4).toMatch(/overflow-x:\s*clip/);
  });
});

describe('G4-7 crisp pixel edges', () => {
  it('the LAST .cabin-canvas rule resolves to pixelated only', () => {
    const blocks = [...css.matchAll(/\.cabin-canvas\s*\{([\s\S]*?)\}/g)];
    const last = blocks[blocks.length - 1][1];
    expect(last).toMatch(/image-rendering:\s*pixelated/);
    expect(last).not.toContain('crisp-edges');
  });
});

describe('G4 cabin-hud safe-area convention', () => {
  it('defines a single .cabin-hud container contract', () => {
    const rule = g4.match(/\.cabin-hud\s*\{([\s\S]*?)\}/)!;
    expect(rule[1]).toMatch(/display:\s*flex/);
    expect(rule[1]).toMatch(/max-height/);
  });
});
