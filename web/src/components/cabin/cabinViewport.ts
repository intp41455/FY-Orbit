/**
 * I3 · 视口 / 外壳共享契约（双形态并行的地基）
 *
 * 背景（为什么需要这个模块）：
 *   施工说明书 §0.1 定死「双形态并行」——① 内嵌工作台（游戏挂进页面容器）
 *   ② 独立全屏（自有路由 + 整屏 HUD），且**二者共用同一游戏核心**。
 *   在 I3 之前，`cabinScene.ts` / `cabinInteriorScene.ts` 的初始视口与 resize
 *   全部硬编码 `window.innerWidth / window.innerHeight`，这意味着游戏核心**只能**
 *   铺满整个浏览器窗口 —— 无法被塞进任何尺寸的容器，「内嵌工作台」无从落地。
 *
 * 本模块做两件事：
 *   1. `resolveViewport(host)` —— 把「视口从哪来」收敛成一个可测的纯函数：
 *      有宿主元素就量宿主，量不到就回退 window（保持既有行为，旧调用零改动）。
 *   2. `observeViewport(host, cb)` —— 把「何时重新量」收敛成一个订阅：
 *      宿主存在时用 ResizeObserver（容器尺寸变化即回调，含侧栏折叠等非 window 变化），
 *      并始终附带 window resize 兜底；返回取消订阅函数。
 *
 * 设计约束（与 §2 铁律一致）：
 *   - 纯逻辑、零 pixi 依赖：可在 jsdom 下直接单测。
 *   - **向后兼容**：`host` 为 null/undefined 或量到 0 时一律回退 window，
 *     因此未传宿主的既有调用方行为完全不变。
 */

/** 视口尺寸（CSS px）。 */
export interface ViewportSize {
  width: number;
  height: number;
}

/** 外壳形态：内嵌工作台 / 独立全屏。二者共用同一游戏核心。 */
export type CabinShellMode = 'embedded' | 'fullscreen';

/** 外壳模式持久化键（切形态后刷新保持）。 */
export const CABIN_SHELL_STORAGE_KEY = 'fy.cabin.shell.v1';

/** 独立全屏路由路径（自有路由 + 整屏 HUD）。 */
export const CABIN_FULLSCREEN_PATH = '/play';

/**
 * 量取宿主元素尺寸；量不到（无宿主 / 未挂载 / 0 尺寸）时回退到 window。
 *
 * @param host 游戏核心的挂载宿主元素；null/undefined 表示「铺满窗口」
 * @param fallback 提供时作为最终兜底（便于测试注入固定视口）
 */
export function resolveViewport(
  host?: Element | null,
  fallback?: ViewportSize,
): ViewportSize {
  if (host) {
    const rect = host.getBoundingClientRect();
    const width = Math.max(0, Math.floor(rect.width));
    const height = Math.max(0, Math.floor(rect.height));
    // 宿主存在但尚未布局（0 尺寸）时不可用：PixiJS 拿到 0 会渲染空白，
    // 因此退回 window 而不是把 0 交给渲染层。
    if (width > 0 && height > 0) return { width, height };
  }
  if (fallback) return { width: fallback.width, height: fallback.height };
  if (typeof window === 'undefined') return { width: 0, height: 0 };
  return { width: window.innerWidth, height: window.innerHeight };
}

/**
 * 订阅视口变化。宿主存在时以 ResizeObserver 为主（容器变化，含非 window 引起的
 * 布局变动），window resize 始终兜底；返回取消订阅函数。
 *
 * 所有回调都重新走一次 `resolveViewport`，保证「读到什么就渲染成什么」。
 */
export function observeViewport(
  host: Element | null | undefined,
  onChange: (size: ViewportSize) => void,
): () => void {
  let disposed = false;
  const emit = () => {
    if (disposed) return;
    onChange(resolveViewport(host));
  };

  let observer: ResizeObserver | null = null;
  if (host && typeof ResizeObserver !== 'undefined') {
    observer = new ResizeObserver(emit);
    observer.observe(host);
  }

  const onWindowResize = () => emit();
  if (typeof window !== 'undefined') {
    window.addEventListener('resize', onWindowResize);
  }

  return () => {
    disposed = true;
    observer?.disconnect();
    if (typeof window !== 'undefined') {
      window.removeEventListener('resize', onWindowResize);
    }
  };
}

/** 读取已保存的外壳模式（非法/缺失一律回退默认 embedded 内嵌工作台）。 */
export function loadShellMode(defaultMode: CabinShellMode = 'embedded'): CabinShellMode {
  if (typeof localStorage === 'undefined') return defaultMode;
  const raw = localStorage.getItem(CABIN_SHELL_STORAGE_KEY);
  return raw === 'fullscreen' || raw === 'embedded' ? raw : defaultMode;
}

/** 保存外壳模式（存储不可用时静默跳过，不阻断游戏）。 */
export function saveShellMode(mode: CabinShellMode): void {
  try {
    localStorage.setItem(CABIN_SHELL_STORAGE_KEY, mode);
  } catch {
    // 隐私模式 / 配额满：切形态本身仍生效，只是不持久化。诚实静默，不伪造成功。
  }
}

/** 决定「当前视口下该用什么外壳」——独立全屏路由永远 fullscreen。 */
export function resolveShellMode(
  routeWantsFullscreen: boolean,
  stored: CabinShellMode,
): CabinShellMode {
  return routeWantsFullscreen ? 'fullscreen' : stored;
}
