import { describe, expect, it, vi, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';

vi.mock('../auth/AuthContext', () => ({
  useAuth: () => ({ owner: { sub: 'abc123', name: '' }, logout: vi.fn() }),
  useOptionalAuth: () => null,
}));
vi.mock('./NotificationBell', () => ({ NotificationBell: () => null }));
vi.mock('./pet/DeskPetCompanion', () => ({ DeskPetCompanion: () => null }));
vi.mock('./ui', async (importOriginal) => {
  const actual = await importOriginal<typeof import('./ui')>();
  return { ...actual, OfflineBadge: () => null };
});

import { Layout } from './Layout';

describe('Layout 侧栏直达', () => {
  beforeEach(() => { window.localStorage.clear(); });

  it('权限与沙箱隔离直达桌面自动化卡，预算与用量直达用量仪表页', () => {
    render(
      <MemoryRouter initialEntries={['/workbench']}>
        <Routes>
          <Route element={<Layout />}>
            <Route path="/workbench" element={<div>workbench</div>} />
          </Route>
        </Routes>
      </MemoryRouter>,
    );
    const perm = screen.getByRole('link', { name: /权限与沙箱隔离/ });
    const budget = screen.getByRole('link', { name: /预算与用量/ });
    expect(perm.getAttribute('href')).toBe('/settings#automation-permissions');
    expect(budget.getAttribute('href')).toBe('/observability');
  });
});
