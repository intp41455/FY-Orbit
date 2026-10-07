import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';

vi.mock('../../api/knowledge', async () => {
  const actual = await vi.importActual<typeof import('../../api/knowledge')>('../../api/knowledge');
  return { ...actual, knowledgeApi: { configureSource: vi.fn(), imaStatus: vi.fn() } };
});

import { knowledgeApi } from '../../api/knowledge';
import { ImaKnowledgeCard } from './ImaKnowledgeCard';

const api = () => knowledgeApi as unknown as Record<string, ReturnType<typeof vi.fn>>;

const STATUS = {
  source_id: 'ima',
  kb_id: '7509748362520236',
  configured: false,
  channels: { mcp: { configured: false }, rest: { configured: false } },
  credentials_present: { app_id: false, api_key: false, secret_key: false, base_url: false },
  cache_path: '',
  detail: '未接入',
};

describe('ImaKnowledgeCard', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    api().imaStatus.mockResolvedValue(STATUS);
  });

  it('填写三件套并保存，调用 configureSource(ima) 并提示加密存储', async () => {
    api().configureSource.mockResolvedValue({
      source_id: 'ima',
      configured: true,
      storage: 'hub_fernet',
      persist_restart: true,
    });
    render(<ImaKnowledgeCard />);
    fireEvent.change(screen.getByLabelText('ima-app_id'), { target: { value: 'app-1' } });
    fireEvent.change(screen.getByLabelText('ima-api_key'), { target: { value: 'key-1' } });
    fireEvent.change(screen.getByLabelText('ima-secret_key'), { target: { value: 'sec-1' } });
    fireEvent.click(screen.getByText('保存 ima 凭证'));
    await waitFor(() =>
      expect(api().configureSource).toHaveBeenCalledWith('ima', {
        app_id: 'app-1',
        api_key: 'key-1',
        secret_key: 'sec-1',
      }),
    );
    expect((await screen.findByTestId('ima-card-notice')).textContent).toContain('加密存储');
  });

  it('密钥字段使用 password 输入', () => {
    render(<ImaKnowledgeCard />);
    expect(screen.getByLabelText('ima-api_key')).toHaveAttribute('type', 'password');
    expect(screen.getByLabelText('ima-secret_key')).toHaveAttribute('type', 'password');
  });

  it('全空提交报错且不请求后端', async () => {
    render(<ImaKnowledgeCard />);
    fireEvent.click(screen.getByText('保存 ima 凭证'));
    expect(await screen.findByTestId('ima-card-error')).toBeInTheDocument();
    expect(api().configureSource).not.toHaveBeenCalled();
  });
});
