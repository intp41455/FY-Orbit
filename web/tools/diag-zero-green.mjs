// Verifies that the zero-green override layer in tokens.css actually WINS over
// the frozen styles.css — by reading COMPUTED styles from a real browser.
//
// Textual greps cannot answer this: styles.css is frozen and still literally
// contains #15803d, but main.tsx imports tokens.css AFTER it, so equal
// specificity should let tokens.css override. This proves it empirically.
//
// Usage: node tools/diag-zero-green.mjs [baseURL]
import { chromium } from 'playwright';

const BASE = process.argv[2] ?? 'http://127.0.0.1:4173';
const TOKEN = process.env.E2E_LOCAL_TOKEN ?? '';

/** sRGB -> HSL, used to decide whether a computed colour reads as green. */
function rgbToHslStr(r, g, b) {
  const rn = r / 255, gn = g / 255, bn = b / 255;
  const max = Math.max(rn, gn, bn), min = Math.min(rn, gn, bn);
  const l = (max + min) / 2;
  let h = 0, s = 0;
  if (max !== min) {
    const d = max - min;
    s = l > 0.5 ? d / (2 - max - min) : d / (max + min);
    if (max === rn) h = ((gn - bn) / d + (gn < bn ? 6 : 0)) / 6;
    else if (max === gn) h = ((bn - rn) / d + 2) / 6;
    else h = ((rn - gn) / d + 4) / 6;
  }
  return { h: Math.round(h * 360), s: +s.toFixed(3), l: +l.toFixed(3) };
}

function isGreen(r, g, b) {
  const { h, s } = rgbToHslStr(r, g, b);
  return s > 0.18 && h >= 75 && h <= 165;
}

const browser = await chromium.launch();
const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 } });
const page = await ctx.newPage();

const auth = await ctx.request.post(`${BASE}/auth/local/dev-token`, { data: { token: TOKEN } });
const m = /fy_session=([^;]+)/.exec(auth.headers()['set-cookie'] ?? '');
if (m) {
  await ctx.addCookies([
    { name: 'fy_session', value: m[1], domain: '127.0.0.1', path: '/', httpOnly: true, sameSite: 'Lax' },
  ]);
}

// Park the token definitions on every page under test.
await page.goto(`${BASE}/login`);
await page.waitForTimeout(600);

const findings = await page.evaluate(() => {
  const read = (sel, props) => {
    const el = document.createElement('div');
    el.className = sel;
    document.body.appendChild(el);
    const cs = getComputedStyle(el);
    const out = Object.fromEntries(props.map((p) => [p, cs.getPropertyValue(p)]));
    el.remove();
    return out;
  };
  return {
    order: {
      stylesCss: [...document.styleSheets].some((s) => (s.href ?? '').includes('index')),
      tokensLoaded: !!getComputedStyle(document.documentElement).getPropertyValue('--ui-st-complete').trim(),
    },
    diffStatAdd: read('diff-stat-add', ['color']),
    diffAdd: read('diff-add', ['background-color', 'color']),
    commitDiffAdd: read('commit-diff-file diff-add', ['background-color']),
    hashVerifyOk: read('hash-verify ok', ['background-color', 'border-color', 'color']),
    pluginRiskLow: read('fy-plugin-risk-low', ['background-color', 'color']),
  };
});

console.log('tokens.css loaded :', findings.order.tokensLoaded, `(${findings.order.tokensLoaded.trim?.() ?? ''})`);
console.log(JSON.stringify(findings, null, 2));

// Parse every colour string back to rgb() and test each for green.
function parseColor(s) {
  const mm = /rgba?\(([^)]+)\)/.exec(s ?? '');
  if (!mm) return null;
  const p = mm[1].split(/[,\s/]+/).filter(Boolean).map(Number);
  return p.length >= 3 ? { r: p[0], g: p[1], b: p[2], a: p[3] ?? 1 } : null;
}

console.log('\n===== COMPUTED-COLOUR GREEN CHECK =====');
let green = 0;
for (const [sel, props] of Object.entries(findings)) {
  if (sel === 'order') continue;
  for (const [prop, val] of Object.entries(props)) {
    const c = parseColor(val);
    if (!c) { console.log(`  ?? ${sel} { ${prop} } = ${val}  (unparsed)`); continue; }
    const g = isGreen(c.r, c.g, c.b);
    if (g) green++;
    const { h, s, l } = rgbToHslStr(c.r, c.g, c.b);
    console.log(`  ${g ? 'GREEN!' : 'ok    '} ${sel} { ${prop} } = rgb(${c.r},${c.g},${c.b})  hsl(${h} ${s} ${l})`);
  }
}
console.log(`\n${green === 0 ? 'PASS - 0 computed green in the zero-green surface' : `FAIL - ${green} computed green`}`);
await browser.close();
process.exit(green === 0 ? 0 : 1);
