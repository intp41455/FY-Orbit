/**
 * P12 · 视觉快照基线（平台域组件的 HTML 级回归锚）。
 *
 * 纪律（工单原文）：快照更新**必须人工 review diff**，禁 `vitest -u` 自动绕过。
 * 本文件是**初始基线**的创建（非 -u 更新）；后续任何快照 diff 必须随 commit
 * 人工审阅并说明理由。快照对象是平台域组件（游戏域归 15 号文档管辖）。
 */
import { describe, it, expect } from 'vitest';
import { render } from '@testing-library/react';
import { PreviewChart } from './PreviewChart';
import { DslDiagnostics } from '../dsl/DslDiagnostics';

const BAR_SPEC = {
  chart: 'bar' as const,
  title: '演练成绩',
  series: [
    { label: '张三', value: 90 },
    { label: '李四', value: 85 },
  ],
};

describe('PreviewChart 视觉快照基线（P12）', () => {
  it('bar 图表的渲染 HTML 与基线一致', () => {
    const { container } = render(<PreviewChart spec={BAR_SPEC} />);
    expect(container.innerHTML).toMatchSnapshot();
  });

  it('line 图表的渲染 HTML 与基线一致', () => {
    const { container } = render(
      <PreviewChart
        spec={{ chart: 'line', series: [{ label: 'a', value: 1 }, { label: 'b', value: 3 }] }}
      />,
    );
    expect(container.innerHTML).toMatchSnapshot();
  });
});

describe('DslDiagnostics 视觉快照基线（P12）', () => {
  it('错误态面板（2 条诊断）的渲染 HTML 与基线一致', () => {
    const { container } = render(
      <DslDiagnostics
        diagnostics={[
          { node_id: 'a', field_path: 'params.op', code: 'string_type', message: 'Input should be a valid string' },
          { node_id: 'b', field_path: 'params.field', code: 'missing', message: 'Field required' },
        ]}
      />,
    );
    expect(container.innerHTML).toMatchSnapshot();
  });

  it('通过态面板（0 条诊断）的渲染 HTML 与基线一致', () => {
    const { container } = render(<DslDiagnostics diagnostics={[]} />);
    expect(container.innerHTML).toMatchSnapshot();
  });
});
