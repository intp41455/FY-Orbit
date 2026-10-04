/**
 * P12 · DslCanvas / DslDiagnostics 无障碍（红点诊断数可感知 / 错误区 role / 按钮可读）。
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { DslCanvas } from './DslCanvas';
import { dslCanvasApi } from '../../api/dslCanvas';

vi.mock('../../api/dslCanvas', () => ({
  dslCanvasApi: {
    run: vi.fn(), validate: vi.fn(), schema: vi.fn(), getRun: vi.fn(),
    validateIr: vi.fn(),
  },
}));

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(dslCanvasApi.validateIr).mockResolvedValue({
    valid: false,
    diagnostics: [
      { node_id: 'input1', field_path: 'params.value', code: 'extra_field', message: 'Extra inputs are not permitted' },
    ],
  });
});

describe('DslCanvas 无障碍（P12）', () => {
  it('节点红点带 aria-label，诊断数量可被读屏感知（非仅视觉红点）', async () => {
    const user = userEvent.setup();
    render(<DslCanvas />);
    await user.click(screen.getByTestId('palette-input'));
    const dot = await screen.findByTestId(/^dsl-node-diag-/);
    expect(dot.getAttribute('aria-label')).toMatch(/1 条类型诊断/);
  });

  it('诊断面板以 role="alert" 呈现错误汇总', async () => {
    const user = userEvent.setup();
    render(<DslCanvas />);
    await user.click(screen.getByTestId('palette-input'));
    await waitFor(() => {
      expect(screen.getByTestId('dsl-ir-diagnostics').getAttribute('role')).toBe('alert');
    });
  });

  it('动作按钮全部有可见文本（不依赖图标/颜色传达功能）', () => {
    render(<DslCanvas />);
    for (const id of ['dsl-generate', 'dsl-run']) {
      const btn = screen.queryByTestId(id);
      if (btn) expect((btn.textContent ?? '').trim().length).toBeGreaterThan(0);
    }
    // 节点面板的三个添加入口都是带文字的按钮语义元素（可聚焦、可回车触发）
    for (const t of ['input', 'transform', 'output']) {
      const item = screen.getByTestId(`palette-${t}`);
      expect(item.textContent?.trim().length ?? 0).toBeGreaterThan(0);
    }
  });

  it('诊断条目文本包含 code 与 message（错误不只以红点存在）', async () => {
    const user = userEvent.setup();
    render(<DslCanvas />);
    await user.click(screen.getByTestId('palette-input'));
    const item = await screen.findByTestId('dsl-ir-item');
    expect(item.textContent).toContain('extra_field');
    expect(item.textContent).toContain('Extra inputs are not permitted');
  });
});
