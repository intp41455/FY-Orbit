import { useEffect, useState } from 'react';
import { NavLink, Outlet, useLocation } from 'react-router-dom';
import { useAuth } from '../auth/AuthContext';
import { OfflineBadge } from './ui';
import { NotificationBell } from './NotificationBell';

/**
 * 工作台优先、个人空间独立切换。团队画布收在「协作画布」内，不新增一级菜单。
 */
type Space = 'workbench' | 'personal';

interface NavItem {
  to: string;
  label: string;
  sub: string;
  icon: string;
  space: Space;
}

const NAV: NavItem[] = [
  { to: '/workbench', label: '任务工作台', sub: '指挥 · 终端 · 验收', icon: '🎯', space: 'workbench' },
  { to: '/canvas', label: '协作画布', sub: '内部团队 · 逐成员选模型', icon: '🧭', space: 'workbench' },
  { to: '/dsl-canvas', label: '工作流工坊', sub: '受限动词 · 数据流执行', icon: '🧱', space: 'workbench' },
  { to: '/chat-debug', label: 'Chat 调试', sub: '模板 · 工具 · 流式联动', icon: '🛠️', space: 'workbench' },
  { to: '/agent-dispatch', label: '子 Agent 派发', sub: 'Task 协议 · 独立验收', icon: '📨', space: 'workbench' },
  { to: '/skills', label: 'Agent 与技能', sub: 'MCP · 经验沉淀', icon: '🧩', space: 'workbench' },
  { to: '/hub', label: '超级中台', sub: '统一适配层 · 能力路由', icon: '🧲', space: 'workbench' },
  { to: '/chat', label: '对话', sub: '流式会话', icon: '💬', space: 'personal' },
  { to: '/cabin', label: '我的小屋', sub: '数码小人 · 经营模拟', icon: '🏠', space: 'personal' },
  { to: '/history', label: '历史', sub: '会话留痕', icon: '🕘', space: 'personal' },
  { to: '/growth', label: '成长记录', sub: '阶段与复盘', icon: '🌱', space: 'personal' },
  { to: '/assessments', label: '测评', sub: '结构化评估', icon: '📐', space: 'personal' },
  { to: '/profiles', label: '多维画像', sub: '个人与对象', icon: '🪞', space: 'personal' },
  { to: '/avatar', label: '角色工坊', sub: '专属像素小人', icon: '🧑‍🎨', space: 'personal' },
  { to: '/knowledge', label: '知识库', sub: '本地文档 RAG · 适配器', icon: '📚', space: 'personal' },
  { to: '/approvals', label: '审批中心', sub: '提案与授权', icon: '✅', space: 'workbench' },
  { to: '/settings', label: '设置与数据', sub: '同步 · 权限', icon: '⚙️', space: 'workbench' },
  { to: '/plugins', label: '插件市场', sub: '签名 · 扫描 · 授权安装', icon: '🛍️', space: 'workbench' },  // P6 追加一项
];

function spaceForPath(pathname: string): Space {
  const hit = NAV.find((n) => pathname.startsWith(n.to));
  return hit?.space ?? 'workbench';
}

export function Layout() {
  const { owner, logout } = useAuth();
  const location = useLocation();
  const derived = spaceForPath(location.pathname);
  const [space, setSpace] = useState<Space>(derived);

  // The active space follows the route so a deep link lands in the right rail,
  // while the manual switch stays available within a space.
  useEffect(() => { setSpace(derived); }, [derived]);

  const items = NAV.filter((n) => n.space === space);

  return (
    <div className="app">
      <aside className="sidebar">
        <div className="brand">
          <div className="brand-mark" aria-hidden="true">FY</div>
          <div className="brand-text">
            <strong>AI 协作工作台</strong>
            <span>让 AI 团队，把事情做完</span>
          </div>
        </div>

        <div className="space-switch" role="group" aria-label="空间切换">
          <button
            type="button"
            className={space === 'workbench' ? 'active' : ''}
            aria-pressed={space === 'workbench'}
            onClick={() => setSpace('workbench')}
          >
            <span aria-hidden="true">💼</span> 工作台空间
          </button>
          <button
            type="button"
            className={space === 'personal' ? 'active' : ''}
            aria-pressed={space === 'personal'}
            onClick={() => setSpace('personal')}
          >
            <span aria-hidden="true">🌌</span> 个人空间
          </button>
        </div>

        <div>
          <div className="nav-label">
            {space === 'workbench' ? '工作台功能导航' : '个人空间导航'}
          </div>
          <nav className="nav" aria-label="主导航">
            {items.map((n) => (
              <NavLink
                key={n.to}
                to={n.to}
                end
                className={({ isActive }) => (isActive ? 'active' : '')}
              >
                <span className="nav-icon" aria-hidden="true">{n.icon}</span>
                <span>
                  {n.label}
                  <span className="nav-sub">{n.sub}</span>
                </span>
              </NavLink>
            ))}
          </nav>
        </div>

        <div className="sidebar-foot">
          <NavLink to="/settings" className="control-chip">
            <span className="row" style={{ gap: '0.45rem' }}>
              <span className="dot sky" aria-hidden="true" />
              权限与沙箱隔离
            </span>
            <span className="badge ok">受控</span>
          </NavLink>
          <NavLink to="/settings" className="control-chip">
            <span className="row" style={{ gap: '0.45rem' }}>
              <span className="dot amber" aria-hidden="true" />
              预算与用量
            </span>
            <span style={{ color: 'var(--amber)', fontWeight: 700 }}>查看</span>
          </NavLink>
          <div className="identity" style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
            <span>{owner?.name ?? owner?.sub ?? 'owner'}</span>
            <NotificationBell />
          </div>
          <button type="button" className="small ghost" onClick={() => void logout()}>
            登出
          </button>
        </div>
      </aside>

      <main className="main">
        <OfflineBadge />
        <Outlet />
      </main>
    </div>
  );
}