#!/usr/bin/env node
// Codemod: rewrite literal rgba()/hex colours in domain stylesheets into
// token-derived color-mix() so every colour is governed by tokens.css.
//
// Why a codemod rather than 60+ hand edits: the mapping is 1:1 and mechanical,
// so a script is auditable and reproducible. It prints every substitution.
//
// Alpha is preserved as the mix percentage, which is exactly equivalent for a
// premultiplied-free srgb mix against `transparent`:
//     rgba(R,G,B,A)  ==  color-mix(in srgb, rgb(R,G,B) A%, transparent)
//
// Usage:
//   node tools/tokenize-colors.mjs                 # dry run, all targets
//   node tools/tokenize-colors.mjs --write         # apply
//   node tools/tokenize-colors.mjs --write path    # apply to one file

import fs from 'node:fs';

const WRITE = process.argv.includes('--write');
const targets = process.argv.slice(2).filter((a) => !a.startsWith('--'));

/** hex (rgb) -> design-system token. Only colours that already exist as tokens. */
const RGB_TO_TOKEN = {
  '3,105,161': '--ui-st-complete', // #0369a1 完成 · 深蓝
  '14,165,233': '--ui-st-running', // #0ea5e9 执行中 · 亮天蓝
  '56,189,248': '--ui-st-verifying', // #38bdf8 验证中 · 冰蓝
  '180,83,9': '--ui-st-waiting', // #b45309 等待 · 琥珀
  '146,64,14': '--ui-st-blocked', // #92400e 阻塞 · 深琥珀
  '220,38,38': '--ui-st-failed', // #dc2626 失败 · 红
  '225,29,72': '--ui-st-rework', // #e11d48 返工 · 珊瑚红
  '109,95,151': '--ui-st-external', // #6d5f97 外部 · 紫灰
  '100,116,139': '--ui-st-paused', // #64748b 暂停 · 蓝灰
  // 薄荷高亮/选中：注意是 --ui-teal-* 而非 --ui-mint-*。
  //   #2dd4bf (45,212,191)  == --ui-teal-400  精确相等
  //   #5eead4 (94,234,212)  == --ui-teal-300  精确相等
  // --ui-mint-400 是 #22d3ee、--ui-mint-300 是 #67e8f9，是偏青的另一条 ramp，
  // 拿它们顶替会让渲染色相发生偏移，违反「转换前后渲染等价」的前提。
  '45,212,191': '--ui-teal-400',
  '94,234,212': '--ui-teal-300',
  '15,23,42': '--ui-ink-1', // 主文字 / 深底
  '255,255,255': '--ui-ink-inv',
  // 两个近乎相同的浅冰蓝底合并到 --ui-sky-100：蓝通道 242 与 247 之差
  // 肉眼不可辨，保留两个字面量只会让裸色清单多两条无意义条目。
  '224,242,254': '--ui-sky-100',
  '224,247,255': '--ui-sky-100',
  // 斑马纹行底色。它就是 --ui-st-paused-bg 的颜色，用该令牌可做到渲染零变化；
  // 语义上「paused」用于中性行底属于命名错位，已在收口报告里留账待改名。
  '241,245,249': '--ui-st-paused-bg',
};

/**
 * Tokens that already carry their own alpha. When substituting a literal whose
 * alpha differs, the mix percentage must be divided by the token's alpha,
 * otherwise the rendered result silently changes.
 *   --ui-st-paused-bg is rgba(241,245,249,.9); asking for .6 means 0.6/0.9 = 67%.
 */
const TOKEN_ALPHA = {
  '--ui-st-paused-bg': 0.9,
  '--ui-st-complete-bg': 0.85,
};

/** Fully opaque hex that already has a token. */
const HEX_TO_TOKEN = {
  '#e0f2ff': '--ui-st-complete-bg',
  '#f1f5f9': '--ui-st-paused-bg',
};

function alphaToPct(a) {
  const n = Number(a);
  if (!Number.isFinite(n)) return null;
  // 0.28 -> 28%, .45 -> 45%, 1 -> 100%
  const pct = Math.round(n * 1000) / 10;
  return Number.isInteger(pct) ? String(pct) : String(pct);
}

function convert(src, rel) {
  const changes = [];
  let out = src;

  // rgba(...) with any spacing style.
  out = out.replace(/rgba?\(\s*([\d.]+)\s*,\s*([\d.]+)\s*,\s*([\d.]+)\s*(?:,\s*([\d.]+)\s*)?\)/g, (full, r, g, b, a) => {
    const key = [r, g, b].map((x) => Number(x)).join(',');
    const token = RGB_TO_TOKEN[key];
    if (!token) return full;
    const alpha = a === undefined ? 1 : Number(a);
    if (alpha === 0) {
      changes.push({ rel, from: full, to: 'transparent', note: 'alpha 0' });
      return 'transparent';
    }
    const tokenAlpha = TOKEN_ALPHA[token] ?? 1;
    if (Math.abs(alpha - tokenAlpha) < 1e-6) {
      changes.push({ rel, from: full, to: `var(${token})`, note: `${key} -> ${token} (exact)` });
      return `var(${token})`;
    }
    const pct = Math.round((alpha / tokenAlpha) * 1000) / 10;
    const to = `color-mix(in srgb, var(${token}) ${pct}%, transparent)`;
    changes.push({
      rel,
      from: full,
      to,
      note: `${key} -> ${token} @${alpha} (token alpha ${tokenAlpha} => ${pct}%)`,
    });
    return to;
  });

  // Bare hex that has a token.
  for (const [hex, token] of Object.entries(HEX_TO_TOKEN)) {
    const re = new RegExp(`${hex}\\b`, 'gi');
    if (re.test(out)) {
      out = out.replace(re, `var(${token})`);
      changes.push({ rel, from: hex, to: `var(${token})`, note: 'opaque hex' });
    }
  }

  return { out, changes };
}

const files = targets.length ? targets : ['src/styles/pages/workbench.css'];

let total = 0;
for (const rel of files) {
  if (!fs.existsSync(rel)) {
    console.error(`missing: ${rel}`);
    process.exitCode = 2;
    continue;
  }
  const src = fs.readFileSync(rel, 'utf8');
  const { out, changes } = convert(src, rel);
  if (!changes.length) {
    console.log(`${rel}: nothing to do`);
    continue;
  }
  const grouped = new Map();
  for (const c of changes) {
    if (!grouped.has(c.from)) grouped.set(c.from, { to: c.to, n: 0 });
    grouped.get(c.from).n++;
  }
  console.log(`\n${rel}: ${changes.length} substitution(s)`);
  for (const [from, { to, n }] of [...grouped.entries()].sort((a, b) => b[1].n - a[1].n)) {
    console.log(`  ${String(n).padStart(2)}x  ${from}\n   ->  ${to}`);
  }
  total += changes.length;
  if (WRITE) {
    fs.writeFileSync(rel, out, 'utf8');
    console.log(`  [written]`);
  }
}

console.log(`\nTOTAL ${total} substitution(s)${WRITE ? ' (applied)' : ' (dry run — pass --write to apply)'}`);
