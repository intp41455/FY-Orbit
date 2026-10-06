#!/usr/bin/env node
// Bare-color auditor for the UI design-system scope.
//
// Correctness notes (both were bugs in the first draft):
//  - CSS files are parsed with PostCSS so comments and quoted strings are
//    structurally excluded, never textually guessed.
//  - TSX/TS files cannot be parsed by PostCSS; they are handled by stripping
//    comments and string/template literals first, then matching value-ish
//    context only.
//  - Token NAMES must never count. `--ui-teal-400` contains the substring
//    `teal` but it is a variable reference, not a literal color. We require
//    the candidate not to be preceded by `--` and not to be part of a
//    `var(--...)` reference.
//
// Usage:
//   node tools/audit-bare-colors.mjs
//   node tools/audit-bare-colors.mjs --scope pages
//   node tools/audit-bare-colors.mjs src/styles/pages/cabin.css

import fs from 'node:fs';
import path from 'node:path';
import postcss from 'postcss';

const args = process.argv.slice(2);
const flag = (n) => args.includes(n);
const positional = args.filter((a) => !a.startsWith('--'));
const ROOT = process.cwd();

// tokens.css is the single-writer token definition file: literals there are the
// source of truth. Pixel-art / game canvas stylesheets are art, not UI chrome.
const ALLOWLIST = new Set([
  'src/styles/tokens.css',
  'src/components/cabin/gameplay/cozyGameplay.css',
]);

const HEX = /#(?:[0-9a-fA-F]{3,4}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})(?![0-9a-zA-Z_-])/g;
const FUNC = /\b(?:rgba?|hsla?|hwb|lab|lch|oklab|oklch|color)\(\s*[0-9]/g;
const NAMED = new RegExp(
  '(?<![-\\w])(?:' +
    'white|black|red|blue|green|lime|gray|grey|silver|maroon|navy|' +
    'teal|olive|aqua|fuchsia|purple|orange|yellow|gold|pink|brown|' +
    'cyan|magenta|crimson|indigo|violet|salmon|coral|khaki|ivory|' +
    'beige|azure|lavender|plum|orchid|turquoise|tan|chocolate' +
  ')(?![-\\w])',
  'g',
);

function walkFiles(dir, out = []) {
  for (const e of fs.readdirSync(dir, { withFileTypes: true })) {
    if (e.name === 'node_modules' || e.name === 'dist' || e.name.startsWith('.')) continue;
    const full = path.join(dir, e.name);
    if (e.isDirectory()) walkFiles(full, out);
    else out.push(full);
  }
  return out;
}

function findAll(text) {
  const found = new Map();
  for (const re of [HEX, FUNC, NAMED]) {
    re.lastIndex = 0;
    let m;
    while ((m = re.exec(text)) !== null) {
      const at = m.index;
      // Reject token references: `--ui-teal-400`, `--teal`.
      if (text.slice(Math.max(0, at - 2), at) === '--') continue;
      // Reject anything still inside a var()/calc() identifier chain.
      const before = text.slice(Math.max(0, at - 40), at);
      if (/var\(\s*--[\w-]*$/.test(before)) continue;
      if (!found.has(m[0])) found.set(m[0], 0);
      found.set(m[0], found.get(m[0]) + 1);
    }
  }
  return found;
}

/**
 * Remove comments only.
 *
 * The first draft also stripped quoted strings, which was wrong: in JSX a
 * quoted attribute value such as stroke="#3a5f8a" IS the value under audit.
 * Comments are the real false-positive source (design notes routinely cite the
 * hex they replaced), so stripping them alone is both necessary and safe.
 * Template literals are preserved too because CSS-in-JS lives there.
 */
function stripCode(src) {
  let out = '';
  let i = 0;
  const n = src.length;
  while (i < n) {
    const two = src.slice(i, i + 2);
    if (two === '//') {
      while (i < n && src[i] !== '\n') i++;
      continue;
    }
    if (two === '/*') {
      i += 2;
      // Emit the newlines inside the block comment so line numbers stay exact.
      while (i < n && src.slice(i, i + 2) !== '*/') {
        if (src[i] === '\n') out += '\n';
        i++;
      }
      i += 2;
      continue;
    }
    out += src[i];
    i++;
  }
  return out;
}

function lineOf(src, index) {
  let line = 1;
  for (let k = 0; k < index && k < src.length; k++) if (src[k] === '\n') line++;
  return line;
}

let targets;
if (positional.length) {
  targets = positional.map((p) => path.resolve(ROOT, p));
} else {
  targets = walkFiles(path.join(ROOT, 'src')).filter((f) => /\.(css|scss|tsx|ts)$/.test(f));
  if (flag('scope')) targets = targets.filter((f) => /[\\/]pages[\\/]/.test(f));
}

const results = [];
let totalDecls = 0;

for (const file of targets) {
  const rel = path.relative(ROOT, file).split(path.sep).join('/');
  if (ALLOWLIST.has(rel)) continue;
  const src = fs.readFileSync(file, 'utf8');
  const isCss = /\.s?css$/.test(file);

  if (isCss) {
    let ast;
    try {
      ast = postcss.parse(src, { from: file });
    } catch (err) {
      console.error(`PARSE FAIL  ${rel}: ${err.message}`);
      process.exitCode = 2;
      continue;
    }
    ast.walkDecls((decl) => {
      if (decl.prop.trim().startsWith('--')) return;
      const colors = findAll(decl.value);
      if (colors.size) {
        totalDecls++;
        results.push({
          rel,
          line: decl.source.start.line,
          prop: decl.prop,
          colors: [...colors.entries()].map(([c, k]) => (k > 1 ? `${c} x${k}` : c)),
        });
      }
    });
  } else {
    const stripped = stripCode(src);
    // Any line still holding a colour literal after comment stripping counts.
    //
    // An earlier draft required the line to look like a style property
    // (`color:` / `fill=` / ...). That missed palette declarations such as
    // `const LANE_COLORS = ['#38bdf8', ...]`, because `COLOR` is followed by
    // `S` and the property regex never matches. A hex literal surviving comment
    // stripping is overwhelmingly a colour, so keying off the literal itself is
    // both simpler and stricter.
    const lines = stripped.split('\n');
    lines.forEach((line, idx) => {
      if (!/#[0-9a-fA-F]{3,8}\b|\b(?:rgba?|hsla?|hwb|lab|lch|oklab|oklch)\(\s*[0-9]/.test(line)) return;
      // A governed line references tokens only; drop var(--…) before matching.
      const colors = findAll(line.replace(/var\(\s*--[\w-]+\s*\)/g, 'VAR'));
      if (colors.size) {
        totalDecls++;
        results.push({
          rel,
          line: idx + 1,
          prop: line.trim().slice(0, 70),
          colors: [...colors.entries()].map(([c, k]) => (k > 1 ? `${c} x${k}` : c)),
        });
      }
    });
  }
}

if (results.length === 0) {
  console.log('OK - 0 bare colors in scope');
} else {
  const byFile = new Map();
  for (const r of results) {
    if (!byFile.has(r.rel)) byFile.set(r.rel, []);
    byFile.get(r.rel).push(r);
  }
  const sorted = [...byFile.entries()].sort((a, b) => b[1].length - a[1].length);
  const summary = flag('summary');
  console.log(`Bare colors: ${totalDecls} assignment(s) across ${byFile.size} file(s)\n`);
  for (const [rel, hits] of sorted) {
    const uniq = new Map();
    for (const h of hits) for (const c of h.colors) uniq.set(c, (uniq.get(c) ?? 0) + 1);
    const palette = [...uniq.entries()].sort((a, b) => b[1] - a[1]).map(([c, k]) => (k > 1 ? `${c}x${k}` : c));
    console.log(`${String(hits.length).padStart(4)}  ${rel}`);
    console.log(`       ${palette.join(' ')}`);
    if (!summary) for (const h of hits) console.log(`       L${h.line}  ${h.colors.join(', ')}   | ${h.prop}`);
  }
  process.exitCode = 1;
}
