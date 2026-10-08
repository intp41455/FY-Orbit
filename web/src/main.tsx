import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import './styles.css';
// 设计系统地基（令牌 + .ui-* 共享层），必须在 styles.css 之后加载以覆盖旧别名。
import './styles/tokens.css';
import './styles/desktop-window.css';
import App from './App';
import { registerSW } from 'virtual:pwa-register';

/**
 * 整窗等比缩放（独立窗口适配）。
 *
 * 独立窗口（Edge `--app=`）与「整个浏览器页」的可用宽度差很多：不做处理时窗口
 * 一变窄，按宽视口设计的布局就会被压缩、文字互相重叠。这里以 1440 CSS px 为基准
 * 等比缩放整个应用（等价于浏览器整页缩放）——窗口宽度变化时内容的布局宽度基本
 * 保持稳定，只是整体变大或变小，效果就和其他桌面软件一致。
 *
 * 比例钳制在 [0.8, 1.1]：下限保证小窗口下字号仍可读，上限避免大屏上内容被
 * 放得过大而浪费可用空间。
 */
const APP_SCALE_BASE_WIDTH = 1440;
const APP_SCALE_MIN = 0.8;
const APP_SCALE_MAX = 1.1;

/* ---------------- 全局字号缩放（Ctrl+滚轮 / Ctrl+±） ----------------
   与窗口等比缩放是两件事：
     - applyAppScale 管「窗口多宽 → 整体多大」，随窗口宽度自动变；
     - 字号缩放管「字多大」，由用户手动调，记住选择，刷新后保持。
   两者都作用在 #root 的 zoom 上，所以必须相乘而不是互相覆盖。 */
const FONT_SCALE_STORAGE_KEY = 'fy.fontScale';
const FONT_SCALE_MIN = 0.85;
const FONT_SCALE_MAX = 1.3;
const FONT_SCALE_STEP = 0.05;

function readFontScale(): number {
  try {
    const raw = window.localStorage.getItem(FONT_SCALE_STORAGE_KEY);
    if (!raw) return 1;
    const n = Number(raw);
    if (!Number.isFinite(n)) return 1;
    return Math.min(FONT_SCALE_MAX, Math.max(FONT_SCALE_MIN, n));
  } catch {
    return 1;
  }
}

let fontScale = readFontScale();

function applyFontScale(): void {
  try {
    window.localStorage.setItem(FONT_SCALE_STORAGE_KEY, String(fontScale));
  } catch {
    // 隐私模式等场景写不进去：缩放仍然生效，只是不持久化。
  }
  applyAppScale();
}

function applyAppScale(): void {
  const root = document.getElementById('root');
  if (!root) return;
  const ratio = window.innerWidth / APP_SCALE_BASE_WIDTH;
  const scale = Math.min(APP_SCALE_MAX, Math.max(APP_SCALE_MIN, ratio));
  // 保留 3 位小数，避免连续 resize 时产生无意义的极小抖动。
  root.style.zoom = (scale * fontScale).toFixed(3);
}

function nudgeFontScale(delta: number): void {
  const next = Math.min(FONT_SCALE_MAX, Math.max(FONT_SCALE_MIN, fontScale + delta));
  // 已经到边界时不再变化，避免用户以为按键坏了。
  if (next === fontScale) return;
  fontScale = next;
  applyFontScale();
  // 把当前字号报出来，让「按加减到底生效了」有据可查。
  window.dispatchEvent(new CustomEvent('fy:fontscale', { detail: { scale: fontScale } }));
}

// Register the service worker. It only precaches the static shell (vite.config
// workbox config). No runtime caching of API / private messages / tokens.
registerSW({ immediate: true });

const container = document.getElementById('root');
if (!container) throw new Error('root element missing');

// 独立窗口等比缩放：必须在首次渲染前施加，避免出现一帧未缩放的抖动。
applyAppScale();
let appScaleTimer = 0;
window.addEventListener('resize', () => {
  window.clearTimeout(appScaleTimer);
  appScaleTimer = window.setTimeout(applyAppScale, 200);
});

/* 快捷键：Ctrl/Cmd + 滚轮 或 Ctrl/Cmd + 「+」「-」「0」。
   输入框聚焦时不拦截 —— 那是用户正在打字，Ctrl+滚轮应该是浏览器原生行为。 */
function isEditableTarget(t: EventTarget | null): boolean {
  if (!(t instanceof HTMLElement)) return false;
  return t.closest('input, textarea, select, [contenteditable="true"]') !== null;
}

window.addEventListener('wheel', (e) => {
  if (!e.ctrlKey && !e.metaKey) return;
  if (isEditableTarget(e.target)) return;
  e.preventDefault();
  nudgeFontScale(e.deltaY < 0 ? FONT_SCALE_STEP : -FONT_SCALE_STEP);
}, { passive: false });

window.addEventListener('keydown', (e) => {
  if (!e.ctrlKey && !e.metaKey) return;
  if (isEditableTarget(e.target)) return;
  const k = e.key;
  if (k === '+' || k === '=' || k === 'Add') {
    e.preventDefault();
    nudgeFontScale(FONT_SCALE_STEP);
  } else if (k === '-' || k === '_' || k === 'Subtract') {
    e.preventDefault();
    nudgeFontScale(-FONT_SCALE_STEP);
  } else if (k === '0') {
    e.preventDefault();
    fontScale = 1;
    applyFontScale();
    window.dispatchEvent(new CustomEvent('fy:fontscale', { detail: { scale: fontScale } }));
  }
});

createRoot(container).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
