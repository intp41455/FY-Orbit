import { Link } from 'react-router-dom';
import type { ReactNode } from 'react';
import { useAuth } from '../auth/AuthContext';
import { Spinner } from './ui';

/**
 * W8 · 会话门禁（由原 `RequireAuth` 改造而来，文件名与导出名保持兼容）。
 *
 * 语义变化，这是账号分层的核心：
 *
 * * **旧行为**：没有 owner 会话就跳 `/login`。这与产品定位冲突——工作台应当
 *   免登录可用（任务书 §2.2）。
 * * **新行为**：`AuthContext` 启动时已静默创建本地游客会话，所以这里绝大多数
 *   情况下 `owner` 都存在。真正需要**正式账号**的只有「个人空间增值入口」
 *   （云同步 / 社区 / 导出分享），由 `<RequireAccount>` 显式把关。
 *
 * 因此 `RequireAuth` 现在只负责「能不能进应用」：后端不可达时如实报错，
 * 绝不静默放行到白屏。
 */
export function RequireAuth({ children }: { children: ReactNode }) {
  const { owner, loading, error, refresh } = useAuth();

  if (loading) return <div className="main"><Spinner label="正在校验会话…" /></div>;
  if (error) {
    return (
      <div className="main">
        <div className="notice danger" role="alert">
          {error}
          <button type="button" onClick={() => void refresh()}>重试连接</button>
        </div>
      </div>
    );
  }
  // No session AND no guest (e.g. guest refused) — AuthContext sets `error` for
  // that case, so reaching here means a genuinely unknown state. Say so rather
  // than bouncing to a login page that cannot help.
  if (!owner) {
    return (
      <div className="main">
        <div className="notice danger" role="alert">
          当前没有可用会话，且无法自动创建本地游客会话。
          <button type="button" onClick={() => void refresh()}>重试</button>
        </div>
      </div>
    );
  }
  return <>{children}</>;
}

/**
 * 正式账号门禁：仅用于「云同步 / 社区 / 导出分享」类入口。
 *
 * 游客（`is_guest`）会被引导去设置页就地升级正式账号——升级是同 id UPDATE，
 * 此前产生的全部数据都保留（任务书 §4）。已注册用户直接放行。
 */
export function RequireAccount({
  children,
  title = '该功能需要正式账号',
  description = '云同步、社区与导出分享需要注册账号。升级后你现在产生的所有数据都会保留。',
}: {
  children: ReactNode;
  title?: string;
  description?: string;
}) {
  const { owner, loading, isGuest, error, refresh } = useAuth();

  if (loading) return <div className="main"><Spinner label="正在校验账号…" /></div>;
  if (error) {
    return (
      <div className="main">
        <div className="notice danger" role="alert">
          {error}
          <button type="button" onClick={() => void refresh()}>重试连接</button>
        </div>
      </div>
    );
  }
  if (!owner) {
    return (
      <div className="main">
        <div className="notice danger" role="alert">当前没有可用会话。</div>
      </div>
    );
  }
  if (isGuest) {
    return (
      <div className="main">
        <div className="notice warn" data-testid="require-account-guest" role="status">
          <strong>{title}</strong>
          <p className="muted">{description}</p>
          <Link className="btn" to="/settings#account">前往设置升级账号</Link>
        </div>
      </div>
    );
  }
  return <>{children}</>;
}
