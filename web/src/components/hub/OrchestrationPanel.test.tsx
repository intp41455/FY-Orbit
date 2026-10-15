import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';

vi.mock('../../api/hub', async () => {
  const actual = await vi.importActual<typeof import('../../api/hub')>('../../api/hub');
  return {
    ...actual,
    hubApi: {
      orchestrateCapabilities: vi.fn(),
      orchestrate: vi.fn(),
      orchestratePipeline: vi.fn(),
    },
  };
});

import { hubApi } from '../../api/hub';
import type { HubConnection, HubOrchestrationResult, HubStepResult } from '../../api/hub';
import { OrchestrationPanel } from './OrchestrationPanel';

const mocked = () => hubApi as unknown as Record<string, ReturnType<typeof vi.fn>>;

function conn(patch: Partial<HubConnection> = {}): HubConnection {
  return {
    id: 'c1',
    name: '演示·DSv4.1Flash',
    kind: 'openai_chat',
    group: 'ai',
    preset_id: '',
    icon: '🔴',
    description: '',
    state: 'active',
    config: {},
    secret_fields: [],
    capabilities: [{ name: '技术架构评估' }],
    has_manifest: false,
    params: null,
    preference: 0,
    health: { ok: true, checked_at: null, latency_ms: 10, detail: '' },
    version: 1,
    created_at: null,
    updated_at: null,
    ...patch,
  };
}

function step(patch: Partial<HubStepResult> = {}): HubStepResult {
  return {
    index: 0,
    step: '第 1 步',
    connection_id: 'c1',
    connection_name: '演示·DSv4.1Flash',
    capability: '技术架构评估',
    ok: true,
    output: { text: '这是一份正式答案。' },
    error: '',
    latency_ms: 1200,
    meta: {},
    ...patch,
  };
}

function result(patch: Partial<HubOrchestrationResult> = {}): HubOrchestrationResult {
  return {
    ok: true,
    mode: 'pipeline',
    steps: [step()],
    step_count: 1,
    succeeded_count: 1,
    failed_count: 0,
    final_output: null,
    error: '',
    duration_ms: 1200,
    pending_interrupts: false,
    ...patch,
  };
}

const PREFLIGHT_OK = {
  agent_count: 4,
  capability_count: 9,
  kinds: ['openai_chat'],
  can_orchestrate: true,
  max_workers: 4,
};

beforeEach(() => {
  mocked().orchestrateCapabilities.mockResolvedValue(PREFLIGHT_OK);
  mocked().orchestrate.mockResolvedValue({ orchestration: result({ mode: 'parallel' }) });
  mocked().orchestratePipeline.mockResolvedValue({ orchestration: result() });
});

describe('OrchestrationPanel', () => {
  it('零 Agent 时前置体检直接拦在跑之前，不让用户跑完一轮拿满屏报错', async () => {
    mocked().orchestrateCapabilities.mockResolvedValue({ ...PREFLIGHT_OK, agent_count: 0, can_orchestrate: false });
    render(<OrchestrationPanel connections={[]} />);
    expect(await screen.findByTestId('hub-orch-no-agent')).toBeTruthy();
    fireEvent.change(screen.getByTestId('hub-orch-task'), { target: { value: '写个方案' } });
    fireEvent.change(screen.getByTestId('hub-orch-bullets'), { target: { value: '技术\n成本' } });
    expect(screen.getByTestId('hub-orch-run').hasAttribute('disabled')).toBe(true);
  });

  it('体检失败展示真实原因，不假装「0 个Agent」', async () => {
    mocked().orchestrateCapabilities.mockRejectedValue(new Error('后端不可达'));
    render(<OrchestrationPanel connections={[conn()]} />);
    const el = await screen.findByTestId('hub-orch-preflight-error');
    expect(el.textContent).toContain('后端不可达');
    // 体检未知 ≠ 不能编排，但也不能假装通过：按钮仍锁着，等用户重试。
    expect(screen.getByTestId('hub-orch-run').hasAttribute('disabled')).toBe(true);
  });

  it('截断降级步高亮为「降级产物」，不让它伪装成正式答案', async () => {
    mocked().orchestratePipeline.mockResolvedValue({
      orchestration: result({
        steps: [
          step({
            output: { text: '用户在思考……' },
            meta: {
              degraded_reason: '正文缺失，已降级返回 reasoning_content：思考过程用光了 max_tokens（completion_tokens=2000）',
              usage: { total_tokens: 2100 },
              attempts: 1,
            },
          }),
        ],
      }),
    });
    render(<OrchestrationPanel connections={[conn()]} />);
    await screen.findByTestId('hub-orch-preflight');
    fireEvent.click(screen.getByTestId('hub-orch-mode-pipeline'));
    fireEvent.change(screen.getByTestId('hub-orch-task'), { target: { value: '评估架构' } });
    fireEvent.click(screen.getByTestId('hub-orch-stage-add'));
    fireEvent.change(screen.getByTestId('hub-orch-stage-instruction-0'), { target: { value: '评估' } });
    fireEvent.click(screen.getByTestId('hub-orch-run'));

    const degraded = await screen.findByTestId('hub-orch-degraded-0');
    expect(degraded.textContent).toContain('降级产物');
    expect(degraded.textContent).toContain('reasoning_content');
    // 这一步 ok=true，但整体结论不能因此写成「全部步骤成功」而不带任何标记。
    expect(screen.getByTestId('hub-orch-step-0').getAttribute('data-tone')).toBe('ok');
    expect(document.body.textContent).toContain('2100 tokens');
  });

  it('部分失败显示成功/失败计数，失败步摊开真实原因', async () => {
    mocked().orchestrate.mockResolvedValue({
      orchestration: result({
        ok: false,
        mode: 'parallel',
        steps: [
          step({ index: 0 }),
          step({
            index: 1,
            connection_name: '演示·SpaceBunny',
            ok: false,
            output: null,
            error: 'all upstream attempts failed',
            meta: { attempts: 3 },
          }),
        ],
        step_count: 2,
        succeeded_count: 1,
        failed_count: 1,
      }),
    });
    render(<OrchestrationPanel connections={[conn()]} />);
    await screen.findByTestId('hub-orch-preflight');
    fireEvent.change(screen.getByTestId('hub-orch-task'), { target: { value: '评估' } });
    fireEvent.change(screen.getByTestId('hub-orch-bullets'), { target: { value: '技术\n体验' } });
    fireEvent.click(screen.getByTestId('hub-orch-run'));

    expect(await screen.findByTestId('hub-orch-verdict')).toBeTruthy();
    expect(screen.getByTestId('hub-orch-verdict').textContent).toBe('未全部成功');
    expect(screen.getByTestId('hub-orch-result').textContent).toContain('1/2 步成功');
    expect(screen.getByTestId('hub-orch-result').textContent).toContain('all upstream attempts failed');
    // 重试次数要能看到：退避重试生效过，用户有权知道打过几次。
    expect(screen.getByTestId('hub-orch-result').textContent).toContain('重试 2 次');
  });

  it('并行模式把 aspects 传给后端；切到顺序模式改传 steps', async () => {
    render(<OrchestrationPanel connections={[conn()]} />);
    await screen.findByTestId('hub-orch-preflight');
    fireEvent.change(screen.getByTestId('hub-orch-task'), { target: { value: '评估' } });
    fireEvent.change(screen.getByTestId('hub-orch-bullets'), { target: { value: '技术\n体验' } });
    fireEvent.click(screen.getByTestId('hub-orch-run'));
    await waitFor(() => expect(mocked().orchestrate).toHaveBeenCalled());
    expect(mocked().orchestrate.mock.calls[0][0]).toMatchObject({
      task: '评估',
      mode: 'parallel',
      aspects: ['技术', '体验'],
    });

    fireEvent.click(screen.getByTestId('hub-orch-mode-sequential'));
    fireEvent.click(screen.getByTestId('hub-orch-run'));
    await waitFor(() => expect(mocked().orchestrate).toHaveBeenCalledTimes(2));
    expect(mocked().orchestrate.mock.calls[1][0]).toMatchObject({ mode: 'sequential', steps: ['技术', '体验'] });
  });

  it('pipeline 每步单独配 max_tokens 并原样送进 params_extra', async () => {
    render(<OrchestrationPanel connections={[conn()]} />);
    await screen.findByTestId('hub-orch-preflight');
    fireEvent.click(screen.getByTestId('hub-orch-mode-pipeline'));
    fireEvent.change(screen.getByTestId('hub-orch-task'), { target: { value: '评估架构' } });
    fireEvent.click(screen.getByTestId('hub-orch-stage-add'));
    fireEvent.change(screen.getByTestId('hub-orch-stage-budget-0'), { target: { value: '4000' } });
    fireEvent.change(screen.getByTestId('hub-orch-stage-cap-0'), { target: { value: '技术架构评估' } });
    fireEvent.change(screen.getByTestId('hub-orch-stage-instruction-0'), { target: { value: '评估' } });
    fireEvent.click(screen.getByTestId('hub-orch-run'));

    await waitFor(() => expect(mocked().orchestratePipeline).toHaveBeenCalled());
    expect(mocked().orchestratePipeline.mock.calls[0][0].stages[0]).toMatchObject({
      connection_id: 'c1',
      capability: '技术架构评估',
      params_extra: { max_tokens: 4000 },
    });
  });

  it('查不到连接名时显示可追溯的 id 前缀，不编造名字', async () => {
    mocked().orchestratePipeline.mockResolvedValue({
      orchestration: result({
        steps: [step({ connection_name: '', connection_id: 'abcdef1234567890' })],
      }),
    });
    render(<OrchestrationPanel connections={[conn()]} />);
    await screen.findByTestId('hub-orch-preflight');
    fireEvent.click(screen.getByTestId('hub-orch-mode-pipeline'));
    fireEvent.change(screen.getByTestId('hub-orch-task'), { target: { value: 'x' } });
    fireEvent.click(screen.getByTestId('hub-orch-stage-add'));
    fireEvent.change(screen.getByTestId('hub-orch-stage-instruction-0'), { target: { value: 'y' } });
    fireEvent.click(screen.getByTestId('hub-orch-run'));

    const item = await screen.findByTestId('hub-orch-step-0');
    expect(item.textContent).toContain('未命名连接（abcdef12…）');
  });

  it('连接被删除后自动把对应步骤摘掉，避免提交时才撞 422', async () => {
    const { rerender } = render(<OrchestrationPanel connections={[conn()]} />);
    await screen.findByTestId('hub-orch-preflight');
    fireEvent.click(screen.getByTestId('hub-orch-mode-pipeline'));
    fireEvent.click(screen.getByTestId('hub-orch-stage-add'));
    expect(screen.getByTestId('hub-orch-stage-0')).toBeTruthy();

    rerender(<OrchestrationPanel connections={[]} />);
    await waitFor(() => expect(screen.queryByTestId('hub-orch-stage-0')).toBeNull());
    expect(screen.getByTestId('hub-orch-stages-empty')).toBeTruthy();
  });

  it('请求层报错（非 200）原样透出，不吞', async () => {
    mocked().orchestrate.mockRejectedValue(new Error('请求失败（HTTP 422）'));
    render(<OrchestrationPanel connections={[conn()]} />);
    await screen.findByTestId('hub-orch-preflight');
    fireEvent.change(screen.getByTestId('hub-orch-task'), { target: { value: 'x' } });
    fireEvent.change(screen.getByTestId('hub-orch-bullets'), { target: { value: 'a' } });
    fireEvent.click(screen.getByTestId('hub-orch-run'));
    const el = await screen.findByTestId('hub-orch-error');
    expect(el.textContent).toContain('HTTP 422');
  });

  it('pipeline 模式提示「不传递上一步上下文」—— 实测踩过的坑，不能只写在文档里', async () => {
    render(<OrchestrationPanel connections={[conn()]} />);
    await screen.findByTestId('hub-orch-preflight');
    // 默认是 parallel，不该出现这条警告
    expect(screen.getByTestId('hub-orch-panel').textContent).not.toContain('不自动传递上一步的输出');
    fireEvent.click(screen.getByTestId('hub-orch-mode-pipeline'));
    const hint = screen.getByTestId('hub-orch-panel').textContent ?? '';
    expect(hint).toContain('不自动传递上一步的输出');
  });

  it('待确认中断单独提示，不混进成功结论', async () => {
    mocked().orchestrate.mockResolvedValue({
      orchestration: result({ ok: true, pending_interrupts: true }),
    });
    render(<OrchestrationPanel connections={[conn()]} />);
    await screen.findByTestId('hub-orch-preflight');
    fireEvent.change(screen.getByTestId('hub-orch-task'), { target: { value: 'x' } });
    fireEvent.change(screen.getByTestId('hub-orch-bullets'), { target: { value: 'a' } });
    fireEvent.click(screen.getByTestId('hub-orch-run'));
    expect(await screen.findByTestId('hub-orch-interrupt')).toBeTruthy();
  });
});