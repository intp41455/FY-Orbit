import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';

vi.mock('../api/hub', async () => {
  const actual = await vi.importActual<typeof import('../api/hub')>('../api/hub');
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
      orchestrateCapabilities: vi.fn(),
      orchestrate: vi.fn(),
      orchestratePipeline: vi.fn(),
    },
  };
});

import { hubApi } from '../api/hub';
import type {
  HubCapabilityRow,
  HubConnection,
  HubOrchestrationResult,
  HubPreset,
  HubRouteCandidate,
} from '../api/hub';
import { HubPage } from './HubPage';

const mocked = () => hubApi as unknown as Record<string, ReturnType<typeof vi.fn>>;

function conn(patch: Partial<HubConnection> = {}): HubConnection {
  return {
    id: 'c1',
    name: 'Ollama 本地',
    kind: 'openai_chat',
    group: 'ai',
    preset_id: 'ollama',
    icon: '🦙',
    description: '本地推理',
    state: 'active',
    config: { base_url: 'http://127.0.0.1:11434/v1', api_key: '****' },
    secret_fields: ['api_key'],
    capabilities: [{ name: 'chat' }, { name: 'local' }],
    has_manifest: false,
    params: null,
    preference: 0,
    health: { ok: null, checked_at: null, latency_ms: null, detail: '' },
    version: 1,
    created_at: null,
    updated_at: null,
    ...patch,
  };
}

const PRESET: HubPreset = {
  id: 'ollama',
  name: 'Ollama 本地模型',
  kind: 'openai_chat',
  group: 'ai',
  icon: '🦙',
  description: '本地推理',
  config: { base_url: 'http://127.0.0.1:11434/v1' },
  credential_fields: [],
  capability_tags: ['chat'],
  capabilities: [],
  implemented: true,
  note: '本地推理无需 API Key。',
};

const PRESET_SKELETON: HubPreset = {
  ...PRESET,
  id: 'baidu_pan',
  name: '百度网盘',
  implemented: false,
  note: '仅骨架，尚未接入。',
};

const CAP_ROW: HubCapabilityRow = {
  connection_id: 'c1',
  connection_name: 'Ollama 本地',
  kind: 'openai_chat',
  group: 'ai',
  icon: '🦙',
  healthy: null,
  state: 'active',
  capability: { name: 'chat', description: '对话补全' },
};

const CANDIDATE: HubRouteCandidate = {
  connection_id: 'c1',
  connection_name: 'Ollama 本地',
  kind: 'openai_chat',
  group: 'ai',
  icon: '🦙',
  healthy: true,
  score: 4.5,
  reasons: ['标签命中：chat', '最近探活通过'],
  capability: { name: 'chat' },
};

/** 编排台的样例结果：1 步成功 + 1 步失败 + 一个降级信号，三种展示路径都覆盖到。 */
const ORCHESTRATION: HubOrchestrationResult = {
  ok: false,
  mode: 'parallel',
  steps: [
    {
      index: 0,
      step: '技术可行性',
      connection_id: 'c1',
      connection_name: 'Ollama 本地',
      capability: 'chat',
      ok: true,
      output: { text: '可行。' },
      error: '',
      latency_ms: 900,
      meta: {},
    },
    {
      index: 1,
      step: '成本估算',
      connection_id: 'c1',
      connection_name: 'Ollama 本地',
      capability: 'chat',
      ok: true,
      output: { text: '思考中…' },
      error: '',
      latency_ms: 1500,
      meta: { degraded_reason: '正文缺失，已降级返回 reasoning_content：思考过程用光了 max_tokens' },
    },
    {
      index: 2,
      step: '体验评估',
      connection_id: 'c1',
      connection_name: 'Ollama 本地',
      capability: 'chat',
      ok: false,
      output: null,
      error: 'all upstream attempts failed',
      latency_ms: 4200,
      meta: { attempts: 3 },
    },
  ],
  step_count: 3,
  succeeded_count: 2,
  failed_count: 1,
  final_output: null,
  error: '',
  duration_ms: 4200,
  pending_interrupts: false,
};

function renderPage() {
  return render(
    <MemoryRouter>
      <HubPage />
    </MemoryRouter>,
  );
}

beforeEach(() => {
  mocked().listConnections.mockResolvedValue({ connections: [conn()], count: 1 });
  mocked().listPresets.mockResolvedValue({ presets: [PRESET, PRESET_SKELETON], count: 2 });
  mocked().listCapabilities.mockResolvedValue({ capabilities: [CAP_ROW], count: 1 });
  mocked().healthCheck.mockResolvedValue({
    connection: conn(),
    report: { ok: true, detail: 'HTTP 200', latency_ms: 12 },
  });
  mocked().invoke.mockResolvedValue({
    connection_id: 'c1',
    action: 'invoke',
    result: { ok: true, output: { text: '你好' }, error: '', latency_ms: 8, meta: {} },
  });
  mocked().route.mockResolvedValue({ hint: '聊天', candidates: [CANDIDATE], count: 1 });
  // 编排台挂在同一页上，前置体检必须给个真实应答，否则它会一直显示体检失败。
  mocked().orchestrateCapabilities.mockResolvedValue({
    agent_count: 1,
    capability_count: 2,
    kinds: ['openai_chat'],
    can_orchestrate: true,
    max_workers: 4,
  });
  mocked().orchestrate.mockResolvedValue({ orchestration: ORCHESTRATION });
  mocked().orchestratePipeline.mockResolvedValue({ orchestration: ORCHESTRATION });
});

describe('HubPage', () => {
  it('空态提示创建入口，不用假连接冒充已接入', async () => {
    mocked().listConnections.mockResolvedValue({ connections: [], count: 0 });
    renderPage();
    expect(await screen.findByTestId('hub-empty')).toBeTruthy();
  });

  it('列出连接、类型与三态健康徽标（未探活 ≠ 可用）', async () => {
    renderPage();
    const card = await screen.findByTestId('hub-card-c1');
    expect(within(card).getByText('Ollama 本地')).toBeTruthy();
    const badge = within(card).getByTestId('hub-health-badge');
    expect(badge.getAttribute('data-tone')).toBe('unknown');
    expect(within(card).getByText('未探活')).toBeTruthy();
  });

  it('探活失败时展示后端给的真实原因，不美化', async () => {
    const failed = conn({
      health: { ok: false, checked_at: '2026-10-04T00:00:00Z', latency_ms: null, detail: 'Connection refused' },
    });
    mocked().listConnections.mockResolvedValue({ connections: [failed], count: 1 });
    mocked().healthCheck.mockResolvedValue({
      connection: failed,
      report: { ok: false, detail: 'Connection refused', latency_ms: 0 },
    });
    renderPage();
    await screen.findByTestId('hub-card-c1');
    expect(screen.getByTestId('hub-health-badge').getAttribute('data-tone')).toBe('fail');
    expect(screen.getByTestId('hub-health-detail').textContent).toContain('Connection refused');
    fireEvent.click(screen.getByText('探活'));
    await waitFor(() => {
      expect(screen.getByTestId('hub-notice').textContent).toContain('Connection refused');
    });
  });

  it('调用失败（200 + ok:false）展示 error 与原始 output', async () => {
    mocked().invoke.mockResolvedValue({
      connection_id: 'c1',
      action: 'invoke',
      result: { ok: false, output: { code: 401 }, error: 'invalid api key', latency_ms: 3, meta: {} },
    });
    renderPage();
    await screen.findByTestId('hub-card-c1');
    fireEvent.click(screen.getByText('调用测试'));
    const panel = await screen.findByTestId('hub-invoke-result');
    expect(panel.textContent).toContain('invalid api key');
    expect(panel.textContent).toContain('401');
    expect(panel.querySelector('strong')?.getAttribute('data-tone')).toBe('fail');
  });

  it('缺凭证的连接禁用「调用测试」按钮', async () => {
    mocked().listConnections.mockResolvedValue({
      connections: [conn({ state: 'needs_credentials' })],
      count: 1,
    });
    renderPage();
    await screen.findByTestId('hub-card-c1');
    expect(screen.getByText('缺凭证')).toBeTruthy();
    expect(screen.getByText('调用测试').hasAttribute('disabled')).toBe(true);
  });

  it('预置库对未接入骨架显式标注，不假装可用', async () => {
    renderPage();
    expect(await screen.findByTestId('hub-preset-ollama')).toBeTruthy();
    expect(screen.getByTestId('hub-preset-skeleton-baidu_pan').textContent).toContain('未接入');
  });

  it('「使用预置」把默认参数与能力回填进新建表单', async () => {
    renderPage();
    await screen.findByTestId('hub-preset-ollama');
    fireEvent.click(within(screen.getByTestId('hub-preset-ollama')).getByText('使用预置'));
    const form = await screen.findByTestId('hub-connection-form');
    expect((form.querySelector('[data-testid="hub-form-name"]') as HTMLInputElement).value).toBe('Ollama 本地模型');
    expect((form.querySelector('[data-testid="hub-form-caps"]') as HTMLInputElement).value).toBe('chat');
  });

  it('未接入预置点开后给出如实提示', async () => {
    renderPage();
    await screen.findByTestId('hub-preset-baidu_pan');
    fireEvent.click(within(screen.getByTestId('hub-preset-baidu_pan')).getByText('使用预置'));
    const notice = await screen.findByTestId('hub-form-notice');
    expect(notice.textContent).toContain('内置骨架');
  });

  it('提交表单把加密字段送进 credentials、明文字段留在 config', async () => {
    mocked().createConnection.mockResolvedValue({ connection: conn() });
    renderPage();
    await screen.findByTestId('hub-new');
    fireEvent.click(screen.getByTestId('hub-new'));
    const form = await screen.findByTestId('hub-connection-form');
    fireEvent.change(form.querySelector('[data-testid="hub-form-name"]') as HTMLInputElement, {
      target: { value: '新连接' },
    });
    // config 明文行：先点「添加参数」再填
    fireEvent.click(within(form).getByText('添加参数'));
    const keyInput = form.querySelector('input[placeholder="键，如 base_url"]') as HTMLInputElement;
    const valInput = form.querySelector('input[placeholder="值"]') as HTMLInputElement;
    fireEvent.change(keyInput, { target: { value: 'base_url' } });
    fireEvent.change(valInput, { target: { value: 'https://api.example.com' } });
    // 凭证密文
    const secret = form.querySelector('input[placeholder="填写新值"]') as HTMLInputElement;
    fireEvent.change(secret, { target: { value: 'sk-plain-value' } });
    fireEvent.click(screen.getByTestId('hub-form-submit'));

    await waitFor(() => {
      expect(mocked().createConnection).toHaveBeenCalled();
    });
    const payload = mocked().createConnection.mock.calls[0][0];
    expect(payload.config.base_url).toBe('https://api.example.com');
    expect(payload.credentials.api_key).toBe('sk-plain-value');
    expect(payload.secret_fields).toContain('api_key');
  });

  it('能力清单渲染 + 移除调用', async () => {
    mocked().unregisterCapability.mockResolvedValue({ connection_id: 'c1', capabilities: [] });
    renderPage();
    const table = await screen.findByTestId('hub-capability-table');
    expect(within(table).getByText('chat')).toBeTruthy();
    fireEvent.click(screen.getByTestId('hub-cap-drop-c1-chat'));
    await waitFor(() => {
      expect(mocked().unregisterCapability).toHaveBeenCalledWith('c1', 'chat');
    });
  });

  it('路由试算展示得分与理由；空结果给出提示而非空白', async () => {
    renderPage();
    fireEvent.change(screen.getByTestId('hub-route-hint'), { target: { value: '帮我聊天' } });
    fireEvent.click(screen.getByTestId('hub-route-run'));
    const item = await screen.findByTestId('hub-route-candidate-c1');
    expect(item.textContent).toContain('得分 4.5');
    expect(item.textContent).toContain('最近探活通过');
  });

  it('路由无命中时明确说明，不显示空白列表', async () => {
    mocked().route.mockResolvedValue({ hint: 'x', candidates: [], count: 0 });
    renderPage();
    fireEvent.change(screen.getByTestId('hub-route-hint'), { target: { value: 'x' } });
    fireEvent.click(screen.getByTestId('hub-route-run'));
    expect(await screen.findByTestId('hub-route-empty')).toBeTruthy();
  });

  it('列表接口失败时展示真实错误并保留重试路径', async () => {
    mocked().listConnections.mockRejectedValue(new Error('后端不可达'));
    renderPage();
    expect(await screen.findByTestId('hub-list-error')).toBeTruthy();
    expect(screen.getByTestId('hub-list-error').textContent).toContain('后端不可达');
  });

  it('manifest 导入失败时把后端原因原样透出', async () => {
    mocked().importManifest.mockRejectedValue(new Error('缺少必填字段 url'));
    renderPage();
    fireEvent.click(screen.getByTestId('hub-manifest-open'));
    fireEvent.change(await screen.findByTestId('hub-manifest-text'), {
      target: { value: 'apiVersion: hub/v1' },
    });
    fireEvent.click(screen.getByTestId('hub-manifest-submit'));
    expect(await screen.findByTestId('hub-manifest-error')).toBeTruthy();
    expect(screen.getByTestId('hub-manifest-error').textContent).toContain('缺少必填字段 url');
  });

  it('分组筛选只显示该组连接', async () => {
    mocked().listConnections.mockResolvedValue({
      connections: [conn(), conn({ id: 'c2', name: '飞书机器人', kind: 'http_webhook', group: 'tool' })],
      count: 2,
    });
    renderPage();
    await screen.findByTestId('hub-card-c2');
    fireEvent.click(screen.getByTestId('hub-filter-tool'));
    expect(screen.queryByTestId('hub-card-c1')).toBeNull();
    expect(screen.getByTestId('hub-card-c2')).toBeTruthy();
  });

  it('页面不提供任何凭证明文展示入口', async () => {
    renderPage();
    await screen.findByTestId('hub-card-c1');
    expect(screen.queryByText('sk-plain-value')).toBeNull();
    // 掩码只以 **** 形态出现
    expect(document.body.textContent).not.toContain('api_key 的明文');
  });

  it('编排台挂在中台页面上，并用当前连接当派工对象', async () => {
    renderPage();
    await screen.findByTestId('hub-card-c1');
    expect(screen.getByTestId('hub-orch-panel')).toBeTruthy();
    fireEvent.click(screen.getByTestId('hub-orch-mode-pipeline'));
    fireEvent.click(screen.getByTestId('hub-orch-stage-add'));
    const select = (await screen.findByTestId('hub-orch-stage-conn-0')) as HTMLSelectElement;
    expect(Array.from(select.options).map((o) => o.value)).toContain('c1');
  });

  it('编排结果里成功/失败/降级三种状态都如实呈现', async () => {
    renderPage();
    await screen.findByTestId('hub-orch-preflight');
    fireEvent.change(screen.getByTestId('hub-orch-task'), { target: { value: '评估这个方案' } });
    fireEvent.change(screen.getByTestId('hub-orch-bullets'), { target: { value: '技术可行性\n成本估算\n体验评估' } });
    fireEvent.click(screen.getByTestId('hub-orch-run'));

    const panel = await screen.findByTestId('hub-orch-result');
    expect(panel.textContent).toContain('2/3 步成功');
    expect(screen.getByTestId('hub-orch-step-2').textContent).toContain('all upstream attempts failed');
    // 截断降级那一步 ok=true，必须额外有标记，否则会被当成正式答案
    expect(screen.getByTestId('hub-orch-step-1').getAttribute('data-tone')).toBe('ok');
    expect(screen.getByTestId('hub-orch-degraded-1').textContent).toContain('reasoning_content');
  });
});
