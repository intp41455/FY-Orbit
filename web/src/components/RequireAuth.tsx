import { Navigate, useLocation } from 'react-router-dom';
import type { ReactNode } from 'react';
import { useAuth } from '../auth/AuthContext';
import { Spinner } from './ui';

export function RequireAuth({ children }: { children: ReactNode }) {
  const { owner, loading, error } = useAuth();
  const location = useLocation();

  if (loading) return <div className="main"><Spinner label="正在校验会话…" /></div>;
  if (error) return <div className="main"><div className="notice danger">{error}</div></div>;
  if (!owner) return <Navigate to="/login" state={{ from: location }} replace />;
  return <>{children}</>;
}
