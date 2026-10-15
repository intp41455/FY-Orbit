/**
 * NEW-2 NOTICE 模块：独立声明页面渲染后端下发的原文与清单。
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { LegalNoticePage } from './LegalNoticePage';
import { legalApi } from '../api/legal';

vi.mock('../api/legal', () => ({
  legalApi: { notice: vi.fn() },
}));

const NOTICE = {
  license: {
    id: 'BSL-1.1',
    holder: 'intp41455',
    change_rule: '各版本首次发布后 4 年自动转为 Apache License 2.0',
    license_file: 'LICENSE',
    notice_file: 'NOTICE',
  },
  notice_text: 'FY Orbit · 星轨 (Find Yourself)\nCopyright 2026 intp41455',
  third_party: {
    backend: ['fastapi', 'sqlalchemy'],
    backend_source: 'pyproject.toml',
    frontend: ['react', 'react-dom'],
    frontend_source: 'web/package.json',
  },
};

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(legalApi.notice).mockResolvedValue(NOTICE);
});

function renderPage() {
  return render(
    <MemoryRouter>
      <LegalNoticePage />
    </MemoryRouter>,
  );
}

describe('LegalNoticePage', () => {
  it('渲染许可证行与 NOTICE 原文', async () => {
    renderPage();
    await waitFor(() => expect(legalApi.notice).toHaveBeenCalled());
    expect(screen.getByTestId('legal-license-line')).toHaveTextContent('BSL-1.1');
    expect(screen.getByTestId('legal-license-line')).toHaveTextContent('intp41455');
    expect(screen.getByTestId('legal-notice-text')).toHaveTextContent('Copyright 2026 intp41455');
  });

  it('渲染前后端第三方清单', async () => {
    renderPage();
    await waitFor(() => expect(legalApi.notice).toHaveBeenCalled());
    expect(screen.getByTestId('legal-backend-deps')).toHaveTextContent('fastapi');
    expect(screen.getByTestId('legal-backend-deps')).toHaveTextContent('sqlalchemy');
    expect(screen.getByTestId('legal-frontend-deps')).toHaveTextContent('react');
  });

  it('接口失败时诚实报错、不渲染空声明', async () => {
    vi.mocked(legalApi.notice).mockRejectedValue(new Error('boom'));
    renderPage();
    await waitFor(() => expect(screen.getByRole('alert')).toBeInTheDocument());
    expect(screen.queryByTestId('legal-notice-text')).toBeNull();
  });
});
