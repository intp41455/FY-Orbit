/**
 * I3 · 独立全屏形态（② 自有路由 + 整屏 HUD）。
 *
 * 与 `/cabin`（内嵌工作台形态）**共用同一个游戏核心 `<CabinPage />`** —— 这里不复制任何
 * 游戏逻辑，只是换了一层外壳：不套 Layout（无侧栏）、铺满视口、提供一个退出全屏的入口。
 *
 * 这正是施工说明书 §0.1「二者共用同一游戏核心——游戏组件必须与外壳解耦，靠 props/容器注入
 * 决定内嵌或独立」的落地：同一个组件，换外壳即换形态。
 */
import { useNavigate } from 'react-router-dom';
import { CabinShell } from '../components/cabin/CabinShell';
import { CabinPage } from './CabinPage';

export function GameStandalonePage() {
  const navigate = useNavigate();
  return (
    <CabinShell
      mode="fullscreen"
      label="独立全屏 · 我的小屋"
      onRequestMode={() => navigate('/cabin')}
    >
      <CabinPage />
    </CabinShell>
  );
}
