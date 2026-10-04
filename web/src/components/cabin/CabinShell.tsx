/**
 * I3 · 双形态外壳（内嵌工作台 / 独立全屏）——游戏核心与外壳解耦的落地点。
 *
 * 为什么需要它：
 *   施工说明书 §0.1 定死「双形态并行」：① 内嵌工作台（游戏挂进页面容器，工作台外壳围绕）
 *   ② 独立全屏（自有路由 + 整屏 HUD），且**二者共用同一游戏核心**。
 *   在 I3 之前，`.cabin-root` 恒为 `position: fixed; inset: 0`，游戏只能铺满整个浏览器窗口，
 *   侧栏与工作台外壳全被盖住 —— 「内嵌」形态在结构上不存在。
 *
 * 本组件只做**外壳该做的事**，不含任何游戏逻辑：
 *   1. 提供一个有确定尺寸的容器（`position: relative`），游戏核心挂进去；
 *   2. 用 `data-mode` 把形态告诉 CSS 与测试，由 CSS 决定铺满视口还是收进容器；
 *   3. 可选渲染形态切换按钮；切换决策交给 `onRequestMode`（由页面决定是原地切换还是跳路由）。
 *
 * 游戏核心（CabinStage / InteriorStage）已经通过 `cabinViewport` 按宿主元素量尺寸，
 * 因此容器一变，场景自动跟随 —— 这就是「容器注入决定内嵌或独立」。
 */
import { useEffect, useState, type ReactNode } from 'react';
import { loadShellMode, saveShellMode, type CabinShellMode } from './cabinViewport';

export interface CabinShellProps {
  /** 外壳形态：embedded=内嵌工作台；fullscreen=独立全屏。 */
  mode: CabinShellMode;
  /** 游戏核心（任意组件，外壳不感知其内容）。 */
  children: ReactNode;
  /**
   * 形态切换请求。不传则不渲染切换按钮（外壳退化为纯容器）。
   * 由调用方决定是否原地切换或跳转路由，外壳不做路由假设 —— 保持可测、无耦合。
   */
  onRequestMode?: (next: CabinShellMode) => void;
  /** 无障碍/测试用标签。 */
  label?: string;
}

/**
 * 外壳偏好持久化（独立全屏 / 内嵌 二选一）。
 * 只记录偏好，不自动改路由 —— 避免在他人并行的路由表上做隐式跳转。
 */
export function useCabinShellPreference(defaultMode: CabinShellMode = 'embedded'): {
  preferred: CabinShellMode;
  setPreferred: (next: CabinShellMode) => void;
} {
  const [preferred, setPreferred] = useState<CabinShellMode>(() => loadShellMode(defaultMode));
  useEffect(() => {
    saveShellMode(preferred);
  }, [preferred]);
  return { preferred, setPreferred };
}

/**
 * 双形态外壳容器。
 *
 * 结构刻意保持扁平：children 原样渲染，只在最外层加一层容器，
 * 这样既有页面的 DOM 与测试（testid 查询）不受影响。
 */
export function CabinShell({ mode, children, onRequestMode, label }: CabinShellProps) {
  const fullscreen = mode === 'fullscreen';
  return (
    <div className="cabin-shell" data-mode={mode} data-testid="cabin-shell" aria-label={label}>
      {children}
      {onRequestMode && (
        <button
          type="button"
          className="cabin-shell-toggle"
          data-testid="cabin-shell-toggle"
          data-target-mode={fullscreen ? 'embedded' : 'fullscreen'}
          aria-label={fullscreen ? '退出独立全屏，回到内嵌工作台' : '切换到独立全屏'}
          title={fullscreen ? '退出全屏（回到内嵌工作台）' : '独立全屏游玩'}
          onClick={() => onRequestMode(fullscreen ? 'embedded' : 'fullscreen')}
        >
          {fullscreen ? '⤡ 退出全屏' : '⛶ 全屏游玩'}
        </button>
      )}
    </div>
  );
}
