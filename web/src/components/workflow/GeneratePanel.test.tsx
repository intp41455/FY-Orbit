import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

import { ApiError } from '../../api/client';

vi.mock('../../api/workflowGen', () => ({
  workflowGenApi: { status: vi.fn(), generate: vi.fn() },
}));

import { workflowGenApi, type WorkflowGenerateResponse } from '../../api/workflowGen';
import { GeneratePanel } from './GeneratePanel';
import type { DslDocument } from '../../api/dslCanvas';

const VALID_DSL: DslDocument = {
  version: '1',
  nodes: [
    { id: 'in1', type: 'input', params: { kind: 'literal', value: [{ text: '示例' }] } },
    { id: 'out1', type: 'output', params: { format: 'text' } },
  ],
  edges: [{ from: 'in1', to: 'out1' }],
};

const okResponse = (over: Partial<WorkflowGenerateResponse> = {}): WorkflowGenerateResponse => ({
  dsl: VALID_DSL,
  model: 'gpt-4o-mini',
  provider_id: 'deepseek',
  attempts: [{ attempt: 1, ok: true, error: '' }],
  usage: { total_tokens: 128 },
  settled_usd: '0.000120',
  degraded_from: '',
  degraded_reason: '',
  ...over,
});

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(workflowGenApi.status).mockResolvedValue({ model_configured: true });
  vi.mocked(workflowGenApi.generate).mockResolvedValue(okResponse());
});

describe('GeneratePanel 一句话生成 · 诚实契约', () => {
  it('挂载即问后端模型状态，如实显示「模型已就绪」', async () => {
    render(<GeneratePanel onGenerated={vi.fn()} />);
    await waitFor(() => expect(workflowGenApi.status).toHaveBeenCalled());
    expect((await screen.findByTestId('flow-model-state')).textContent).toBe('模型已就绪');
  });

  it('后端报未配置模型时，状态徽标如实显示「未配置模型」', async () => {
    vi.mocked(workflowGenApi.status).mockResolvedValue({ model_configured: false });
    render(<GeneratePanel onGenerated={vi.fn()} />);
    expect((await screen.findByTestId('flow-model-state')).textContent).toBe('未配置模型');
  });

  it('空 prompt 不打后端，直接如实报错', async () => {
    const user = userEvent.setup();
    render(<GeneratePanel onGenerated={vi.fn()} />);
    await user.click(screen.getByTestId('flow-generate-run'));
    expect((await screen.findByTestId('flow-generate-error')).textContent).toContain('请先描述');
    expect(workflowGenApi.generate).not.toHaveBeenCalled();
  });

  it('503 未配置模型：显示真实错误 + 配置指引，绝不伪造成功', async () => {
    const user = userEvent.setup();
    vi.mocked(workflowGenApi.generate).mockRejectedValue(
      new ApiError(503, {
        code: 'model_not_configured',
        message: 'No model provider is configured for workflow generation.',
      }, 'fallback'),
    );
    const onGenerated = vi.fn();
    render(<GeneratePanel onGenerated={onGenerated} />);
    await screen.findByTestId('flow-model-state');

    await user.type(screen.getByTestId('flow-generate-prompt'), '每天读文件并发邮件');
    await user.click(screen.getByTestId('flow-generate-run'));

    const err = await screen.findByTestId('flow-generate-error');
    expect(err.textContent).toContain('No model provider is configured');
    expect(err.textContent).toContain('FY_MODEL_API_KEY');
    // 诚实红线：没生成成功就绝不能回调父组件、不能显示成功 meta
    expect(onGenerated).not.toHaveBeenCalled();
    expect(screen.queryByTestId('flow-generate-meta')).toBeNull();
    expect(screen.getByTestId('flow-model-state').textContent).toBe('未配置模型');
  });

  it('422 校验失败：原样展示后端真实校验错误，并说明已自动重试', async () => {
    const user = userEvent.setup();
    vi.mocked(workflowGenApi.generate).mockRejectedValue(
      new ApiError(422, {
        code: 'workflow_generation_failed',
        message: "模型在2 次尝试后仍未给出合法 DSL：transform 节点 tf1 verb 必须是 ('map','filter','template')",
        details: {},
      }, 'fallback'),
    );
    const onGenerated = vi.fn();
    render(<GeneratePanel onGenerated={onGenerated} />);
    await screen.findByTestId('flow-model-state');

    await user.type(screen.getByTestId('flow-generate-prompt'), '每天读文件并发邮件');
    await user.click(screen.getByTestId('flow-generate-run'));

    const err = await screen.findByTestId('flow-generate-error');
    expect(err.textContent).toContain('仍未给出合法 DSL');
    expect(err.textContent).toContain("('map','filter','template')");
    expect(err.textContent).toContain('已自动重试一次');
    expect(onGenerated).not.toHaveBeenCalled();
  });

  it('成功：把真实 DSL 交给父组件，并显示实际 provider:model 与费用', async () => {
    const user = userEvent.setup();
    const onGenerated = vi.fn();
    render(<GeneratePanel onGenerated={onGenerated} />);
    await screen.findByTestId('flow-model-state');

    await user.type(screen.getByTestId('flow-generate-prompt'), '每天读文件并发邮件');
    await user.click(screen.getByTestId('flow-generate-run'));

    await waitFor(() => expect(onGenerated).toHaveBeenCalledWith(VALID_DSL, '每天读文件并发邮件'));
    const meta = screen.getByTestId('flow-generate-meta').textContent ?? '';
    expect(meta).toContain('deepseek:gpt-4o-mini');
    expect(meta).toContain('2 节点');
    expect(meta).toContain('1 边');
    expect(meta).toContain('0.000120 USD');
    expect(screen.queryByTestId('flow-generate-error')).toBeNull();
  });

  it('发生降级时明确标注实际由谁应答，不让用户误以为用的是他请求的模型', async () => {
    const user = userEvent.setup();
    vi.mocked(workflowGenApi.generate).mockResolvedValue(okResponse({
      model: 'qwen-plus',
      provider_id: 'alibaba-fallback',
      degraded_from: 'deepseek:gpt-4o-mini',
      degraded_reason: '主 provider 超时',
    }));
    render(<GeneratePanel onGenerated={vi.fn()} />);
    await screen.findByTestId('flow-model-state');

    await user.type(screen.getByTestId('flow-generate-prompt'), '每天读文件并发邮件');
    await user.click(screen.getByTestId('flow-generate-run'));

    const meta = await screen.findByTestId('flow-generate-meta');
    expect(meta.textContent).toContain('alibaba-fallback:qwen-plus');
    expect(meta.textContent).toContain('deepseek:gpt-4o-mini');
    expect(meta.textContent).toContain('主 provider 超时');
  });

  it('重试次数如实反映在后端返回的 attempts 上', async () => {
    const user = userEvent.setup();
    vi.mocked(workflowGenApi.generate).mockResolvedValue(okResponse({
      attempts: [
        { attempt: 1, ok: false, error: '模型输出不是合法 JSON' },
        { attempt: 2, ok: true, error: '' },
      ],
    }));
    render(<GeneratePanel onGenerated={vi.fn()} />);
    await screen.findByTestId('flow-model-state');
    await user.type(screen.getByTestId('flow-generate-prompt'), '筛出年龄大于 30 的记录');
    await user.click(screen.getByTestId('flow-generate-run'));
    expect((await screen.findByTestId('flow-generate-meta')).textContent).toContain('尝试 2 次');
  });

  it('status 接口挂掉时显示「模型状态未知」而不是谎称已就绪', async () => {
    vi.mocked(workflowGenApi.status).mockRejectedValue(new Error('network down'));
    render(<GeneratePanel onGenerated={vi.fn()} />);
    expect((await screen.findByTestId('flow-model-state')).textContent).toBe('模型状态未知');
  });

  it('网络层异常（非 ApiError）如实显示异常信息，不吞掉', async () => {
    const user = userEvent.setup();
    vi.mocked(workflowGenApi.generate).mockRejectedValue(new TypeError('Failed to fetch'));
    render(<GeneratePanel onGenerated={vi.fn()} />);
    await screen.findByTestId('flow-model-state');
    await user.type(screen.getByTestId('flow-generate-prompt'), '每天读文件并发邮件');
    await user.click(screen.getByTestId('flow-generate-run'));
    expect((await screen.findByTestId('flow-generate-error')).textContent).toContain('Failed to fetch');
  });
});
