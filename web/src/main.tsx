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

function applyAppScale(): void {
  const root = document.getElementById('root');
  if (!root) return;
  const ratio = window.innerWidth / APP_SCALE_BASE_WIDTH;
  const scale = Math.min(APP_SCALE_MAX, Math.max(APP_SCALE_MIN, ratio));
  // 保留 3 位小数，避免连续 resize 时产生无意义的极小抖动。
  root.style.zoom = scale.toFixed(3);
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

createRoot(container).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
