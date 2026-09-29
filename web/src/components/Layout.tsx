import { NavLink, Outlet } from 'react-router-dom';
import { useAuth } from '../auth/AuthContext';
import { OfflineBadge } from './ui';

const NAV = [
  { to: '/chat', label: '对话' },
  { to: '/history', label: '历史' },
  { to: '/growth', label: '成长记录' },
  { to: '/assessments', label: '测评' },
  { to: '/workbench', label: '任务工作台' },
  { to: '/approvals', label: '审批中心' },
  { to: '/skills', label: '能力目录' },
  { to: '/private', label: '私人空间' },
  { to: '/settings', label: '设置与数据' },
];

export function Layout() {
  const { owner, logout } = useAuth();
  return (
    <div className="app">
      <aside className="sidebar">
        <h1>Find Yourself</h1>
        <nav className="nav" aria-label="Primary">
          {NAV.map((n) => (
            <NavLink key={n.to} to={n.to} end className={({ isActive }) => (isActive ? 'active' : '')}>
              {n.label}
            </NavLink>
          ))}
        </nav>
        <div style={{ marginTop: '1.5rem' }} className="muted">
          {owner?.name ?? owner?.sub ?? 'owner'}
        </div>
        <button className="small" style={{ marginTop: '0.5rem' }} onClick={() => void logout()}>
          登出
        </button>
      </aside>
      <main className="main">
        <OfflineBadge />
        <Outlet />
      </main>
    </div>
  );
}
