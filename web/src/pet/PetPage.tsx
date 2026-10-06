/**
 * W10-A · 桌面宠物浮窗入口页（路由 `/pet`）· 包 F 视觉层。
 *
 * 刻意不套主 Layout、不要求登录：浮窗是桌面宠物，游客也能看到自己的宠物。
 *
 * 任务书 §4 三条硬约束在这里落地：
 *  1. 根节点 `.pet-root` 背景透明，由 Tauri 窗口的 `transparent: true` 透出桌面；
 *  2. 全域禁止 backdrop-filter —— 透明窗上毛玻璃会渲染成脏灰块；
 *  3. `.pet-root` 整体 `pointer-events: none`，只有宠物本体与控制条恢复命中测试，
 *     透明区域不吃掉桌面点击。
 *
 * 桥接与 `PetWidget` 同一条 Tauri 契约（`window.__fyPetBridge`），浏览器预览降级为
 * `location.assign` + `fy:pet-hide` 事件。不改 `PetWidget.tsx`（不在包 F 可写清单）。
 */
import { PetWidget, type PetBridge } from './PetWidget';
import { LineIcon } from '../components/ui/LineIcon';
import '../styles/pages/system.css';

/** 与 PetWidget 内部同一份降级逻辑；Tauri preload 注入时以注入的为准。 */
function resolveBridge(): PetBridge {
  const g = window as unknown as { __fyPetBridge?: PetBridge };
  return (
    g.__fyPetBridge ?? {
      openRoute: (route) => window.location.assign(route),
      hideWindow: () => window.dispatchEvent(new CustomEvent('fy:pet-hide')),
    }
  );
}

export default function PetPage() {
  const bridge = resolveBridge();

  return (
    <div className="pet-root">
      {/* 悬停/聚焦才显形，平时零遮挡（最少点击守则第 5 条） */}
      <div className="pet-controls">
        <button
          type="button"
          className="pet-ctl"
          title="打开小屋"
          aria-label="打开小屋"
          onClick={() => bridge.openRoute('/cabin')}
        >
          <LineIcon name="cabin" size={16} />
        </button>
        <button
          type="button"
          className="pet-ctl"
          title="隐藏浮窗"
          aria-label="隐藏浮窗"
          onClick={() => bridge.hideWindow()}
        >
          <LineIcon name="close" size={16} />
        </button>
      </div>

      <div className="pet-stage">
        <PetWidget />
      </div>
    </div>
  );
}
