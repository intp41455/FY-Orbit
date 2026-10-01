import { useState } from 'react';
import { useAuth } from '../auth/AuthContext';
import { Navigate } from 'react-router-dom';

export function LoginPage() {
  const { owner, loading, error, login, loginWithToken } = useAuth();
  const [token, setToken] = useState('dev-token-secret');
  const [submittingToken, setSubmittingToken] = useState(false);

  if (loading) return <div className="main">Loading…</div>;
  if (owner) return <Navigate to="/chat" replace />;

  async function handleLocalLogin(e: React.FormEvent) {
    e.preventDefault();
    if (!token.trim()) return;
    setSubmittingToken(true);
    try {
      await loginWithToken(token.trim());
    } finally {
      setSubmittingToken(false);
    }
  }

  return (
    <div className="main" style={{ maxWidth: 460, margin: '0 auto', marginTop: '12vh' }}>
      <div className="card">
        <h2 style={{ marginTop: 0 }}>Find Yourself</h2>
        <p className="muted">
          单所有者自我探索陪伴系统。生产环境通过后端 OIDC 登录；本地桌面单机体验可直接使用本地专属口令登录。
        </p>

        {error && (
          <div className="notice warn" style={{ marginBottom: '1rem' }}>
            {error}
          </div>
        )}

        <div style={{ marginBottom: '1.5rem' }}>
          <button className="primary" onClick={login} style={{ width: '100%' }}>
            使用 OIDC 登录
          </button>
        </div>

        <div style={{ borderTop: '1px solid var(--border)', paddingTop: '1.2rem' }}>
          <h4 style={{ margin: '0 0 0.5rem 0', fontSize: '0.95rem' }}>本地 / 桌面单机口令登录</h4>
          <p className="muted" style={{ fontSize: '0.85rem', marginBottom: '0.8rem' }}>
            在 127.0.0.1 环回地址运行，通过本地环境变量或配置文件授权。
          </p>
          <form onSubmit={handleLocalLogin}>
            <div style={{ marginBottom: '0.8rem' }}>
              <input
                type="password"
                placeholder="本地访问口令 (默认: dev-token-secret)"
                value={token}
                onChange={(e) => setToken(e.target.value)}
                style={{ width: '100%', boxSizing: 'border-box' }}
              />
            </div>
            <button
              type="submit"
              className="primary"
              disabled={submittingToken || !token.trim()}
              style={{ width: '100%' }}
            >
              {submittingToken ? '登录中…' : '本地口令直接登录'}
            </button>
          </form>
        </div>

        <p className="muted" style={{ marginTop: '1.2rem', fontSize: '0.8rem' }}>
          会话校验由后端完成，未配置 OIDC 或口令不匹配时不会通过授权。
        </p>
      </div>
    </div>
  );
}
