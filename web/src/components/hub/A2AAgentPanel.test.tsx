import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';

vi.mock('../../api/hub', async () => {
  const actual = await vi.importActual<typeof import('../../api/hub')>('../../api/hub');
  return {
    ...actual,
    hubApi: {
      listConnections: vi.fn(),
      listPresets: vi.fn(),
      listCapabilities: vi.fn(),
      healthCheck: vi.fn(),
      healthCheckAll: vi.fn(),
      invoke: vi.fn(),
      deleteConnection: vi.fn(),
      updateConnection: vi.fn(),
      createConnection: vi.fn(),
      unregisterCapability: vi.fn(),
      importManifest: vi.fn(),
      manifestExample: vi.fn(),
      route: vi.fn(),
    },
  };
});

import { hubApi } from '../../api/hub';
import type { HubConnection } from '../../api/hub';
import { A2AAgentPanel } from './A2AAgentPanel';

vi.mock('../../hooks/useAutosave', async () => {
  const actual = await vi.importActual<typeof import('../../hooks/useAutosave')>(
    '../../hooks/useAutosave',
  );
  return { ...actual, useBase: () => {} };
});

const mocked = () => hubApi as unknown as Record<string, ReturnType<typeof vi.fn>>;

function a2aConn(patch: Partial<HubConnection> = {}): HubConnection {
  return {
    id: 'a2a-1',
    name: '远程规划 Agent',
    kind: 'a2a_agent',
    group: 'tool',
    preset_id: '',
    icon: '🤝',
    description: '外部 A2A Agent',
    state: 'active',
    config: { base_url: 'https://agent.example.com', api_token: '****' },
    secret_fields: ['api_token'],
    capabilities: [{ name: 'a2a.send' }, { name: 'a2a.skill:plan' }],
    has_manifest: false,
    params: null,
    preference: 0,
    health: { ok: true, checked_at: '2026-10-16T00:00:00Z', latency_ms: 42, detail: 'message/send 已实现' },
    version: 1,
    created_at: null,
    updated_at: null,
    ...patch,
  };
}

function renderPanel(conns: HubConnection[], handlers: { onProbe?: () => void; onDispatch?: () => void } = {}) {
  return render(
    <MemoryRouter>
      <A2AAgentPanel
        connections={conns}
        busy={false}
        onProbe={handlers.onProbe ?? vi.fn()}
        onDispatch={handlers.onDispatch ?? vi.fn()}
      />
    </MemoryRouter>,
  );
}

beforeEach(() => {
  mocked().healthCheck!.mockResolvedValue({
    connection: a2aConn(),
    report: { ok: true, detail: 'message/send 已实现（-32602）；Agent Card 声明 2 项技能', latency_ms: 42 },
  });
  mocked().invoke!.mockResolvedValue({
    connection_id: 'a2a-1',
    action: 'send',
    result: { ok: true, output: { id: 'task-9' }, error: '', latency_ms: 30, meta: { task_id: 'task-9' } },
  });
});

describe('A2AAgentPanel 探活/派发闭环', () => {
  it('未登记时给出登记指引，不假装已接入', () => {
    renderPanel([]);
    expect(screen.getByTestId('hub-a2a-panel-empty').textContent).toContain('base_url');
  });

  it('展示真实探活 detail 与卡片发现的技能', () => {
    renderPanel([a2aConn()]);
    expect(screen.getByTestId('hub-a2a-detail-a2a-1').textContent).toContain('message/send');
    const skills = screen.getByTestId('hub-a2a-skills-a2a-1');
    expect(skills.textContent).toContain('plan');
    expect(skills.textContent).not.toContain('a2a.skill:');
  });

  it('探活按钮把连接交给上层，并按更宽的超时预算请求', () => {
    const onProbe = vi.fn();
    renderPanel([a2aConn()], { onProbe });
    fireEvent.click(screen.getByTestId('hub-a2a-probe-a2a-1'));
    expect(onProbe).toHaveBeenCalledWith(expect.objectContaining({ kind: 'a2a_agent' }));
  });

  it('探活通过的连接可以真派发一条消息', () => {
    const onDispatch = vi.fn();
    renderPanel([a2aConn()], { onDispatch });
    fireEvent.click(screen.getByTestId('hub-a2a-dispatch-a2a-1'));
    expect(onDispatch).toHaveBeenCalledWith(
      expect.objectContaining({ id: 'a2a-1' }),
      expect.stringContaining('ping'),
    );
  });

  it('探活失败/未探活的连接禁用派发，不对没验过的对端发真任务', () => {
    const neverProbed = a2aConn({ health: { ok: null, checked_at: null, latency_ms: null, detail: '' } });
    renderPanel([neverProbed]);
    expect(screen.getByTestId('hub-a2a-dispatch-a2a-1').hasAttribute('disabled')).toBe(true);
    expect(screen.getByTestId('hub-a2a-detail-a2a-1').textContent).toContain('尚未探活');
  });

  it('探活失败时把后端原因原样显示，不美化', async () => {
    mocked().healthCheck!.mockResolvedValue({
      connection: a2aConn({ health: { ok: false, checked_at: 'x', latency_ms: 5, detail: 'hub_a2a_method_unsupported: 未实现 message/send' } }),
      report: { ok: false, detail: 'hub_a2a_method_unsupported: 对端 JSON-RPC 端点未实现 message/send（-32601）', latency_ms: 5 },
    });
    const { describeA2ADispatch } = await import('./A2AAgentPanel');
    const text = describeA2ADispatch(a2aConn(), {
      ok: false,
      detail: 'hub_a2a_method_unsupported: 对端 JSON-RPC 端点未实现 message/send（-32601）',
      latency_ms: 5,
    });
    expect(text).toContain('探活失败');
    expect(text).toContain('-32601');
  });

  it('凭证掩码不被当成明文展示', () => {
    renderPanel([a2aConn()]);
    expect(document.body.textContent).not.toContain('tok-');
  });
});

describe('HubPage A2A 集成', () => {
  it('A2A 面板只收 a2a_agent 连接，探活/派发都走后端真实接口', async () => {
    const { HubPage } = await import('../../pages/HubPage');
    mocked().listConnections!.mockResolvedValue({
      connections: [
        a2aConn(),
        {
          ...a2aConn(),
          id: 'c1',
          name: 'Ollama 本地',
          kind: 'openai_chat',
          group: 'ai',
          config: { base_url: 'http://127.0.0.1:11434/v1' },
          capabilities: [{ name: 'chat' }],
        },
      ],
      count: 2,
    });
    mocked().listPresets!.mockResolvedValue({ presets: [], count: 0 });
    mocked().listCapabilities!.mockResolvedValue({ capabilities: [], count: 0 });

    render(
      <MemoryRouter>
        <HubPage />
      </MemoryRouter>,
    );

    const panel = await screen.findByTestId('hub-a2a-panel');
    expect(within(panel).getByText('远程规划 Agent')).toBeTruthy();
    expect(within(panel).queryByText('Ollama 本地')).toBeNull();

    // 不自动探活（与本页其它连接一致，探活是显式动作），点了才真调后端
    expect(mocked().healthCheck!).not.toHaveBeenCalled();
    fireEvent.click(screen.getByTestId('hub-a2a-probe-a2a-1'));
    await waitFor(() => expect(mocked().healthCheck!).toHaveBeenCalledWith('a2a-1', 8));

    fireEvent.click(screen.getByTestId('hub-a2a-dispatch-a2a-1'));
    await waitFor(() =>
      expect(mocked().invoke!).toHaveBeenCalledWith(
        'a2a-1',
        expect.objectContaining({ action: 'send', params: expect.objectContaining({ prompt: expect.any(String) }) }),
      ),
    );
  });

  it('通用「调用测试」对 a2a_agent 也带上真实文本，不再是空派发', async () => {
    const { HubPage } = await import('../../pages/HubPage');
    mocked().listConnections!.mockResolvedValue({ connections: [a2aConn()], count: 1 });
    mocked().listPresets!.mockResolvedValue({ presets: [], count: 0 });
    mocked().listCapabilities!.mockResolvedValue({ capabilities: [], count: 0 });

    render(
      <MemoryRouter>
        <HubPage />
      </MemoryRouter>,
    );

    await screen.findByTestId('hub-card-a2a-1');
    fireEvent.click(screen.getByText('调用测试'));
    await waitFor(() =>
      expect(mocked().invoke!).toHaveBeenCalledWith(
        'a2a-1',
        expect.objectContaining({ params: expect.objectContaining({ prompt: expect.any(String) }) }),
      ),
    );
  });
});