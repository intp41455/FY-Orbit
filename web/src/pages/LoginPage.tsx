import { useAuth } from '../auth/AuthContext';
import { Navigate } from 'react-router-dom';

export function LoginPage() {
  const { owner, loading, login } = useAuth();

  if (loading) return <div className="main">Loading…</div>;
  if (owner) return <Navigate to="/chat" replace />;

  return (
    <div className="main" style={{ maxWidth: 460, margin: '0 auto', marginTop: '15vh' }}>
      <div className="card">
        <h2 style={{ marginTop: 0 }}>Find Yourself</h2>
        <p className="muted">
          单所有者自我探索陪伴系统。登录通过后端 OIDC 完成；本页面不收集密码，也不在前端存储令牌。
        </p>
        <button className="primary" onClick={login}>
          使用 OIDC 登录
        </button>
        <p className="muted" style={{ marginTop: '1rem' }}>
          离线时无法登录。会话校验由后端完成，未配置凭据时不会显示“登录成功”。
        </p>
      </div>
    </div>
  );
}
