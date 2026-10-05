/**
 * I3 · 独立全屏形态（② 自有路由 + 整屏 HUD）。
 *
 * 与 `/cabin`（内嵌工作台形态）**共用同一个游戏核心 `<CabinPage />`** —— 这里不复制任何
 * 游戏逻辑，只是换了一层外壳：不套 Layout（无侧栏）、铺满视口、提供退出全屏入口。
 *
 * 包 D 视觉层补充：快捷键守则（第 1 条）
 *   - `F11`：浏览器级全屏，页面无权接管，所以只监听它来给出手势提示；
 *   - `Esc`：由本外壳接管 —— 立刻退回 `/cabin` 内嵌工作台，
 *     因为原生 `Esc` 只能退出**浏览器**全屏，退出不了这条自定义路由。
 */
import { useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
import { CabinShell } from '../components/cabin/CabinShell';
import { CabinPage } from './CabinPage';
import '../styles/pages/cabin.css';

export function GameStandalonePage() {
  const navigate = useNavigate();

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== 'Escape') return;
      // Esc 退出独立全屏形态 → 回内嵌工作台（同一份游戏核心，只换外壳）
      navigate('/cabin');
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [navigate]);

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