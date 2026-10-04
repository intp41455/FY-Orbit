/**
 * P2 · Layer 4 图表渲染测试（解析纯函数 + SVG 组件）。
 *
 * 契约：可渲染数据 → 确定性 SVG；不可渲染数据 → ChartSpecError
 * （由 PreviewPane 转成显式错误面板，绝不空白冒充成功）。
 */
import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import {
  ChartSpecError,
  PreviewChart,
  parseRenderSpec,
} from './PreviewChart';

describe('parseRenderSpec · JSON', () => {
  it('合法 bar 数据解析出 series 与 title', () => {
    const spec = parseRenderSpec(
      '{"chart":"bar","title":"成绩","series":[{"label":"张三","value":90},{"label":"李四","value":85.5}]}',
      'data/scores.json',
    );
    expect(spec.chart).toBe('bar');
    expect(spec.title).toBe('成绩');
    expect(spec.series).toHaveLength(2);
    expect(spec.series[0]).toEqual({ label: '张三', value: 90 });
  });

  it('未知图型 / 空 series / 非数值 value / 非字符串 label 都显式抛错', () => {
    expect(() => parseRenderSpec('{"chart":"pie3d","series":[{"label":"a","value":1}]}', 'x.json'))
      .toThrow(ChartSpecError);
    expect(() => parseRenderSpec('{"chart":"pie","series":[]}', 'x.json')).toThrow(ChartSpecError);
    expect(() => parseRenderSpec('{"chart":"pie","series":[{"label":"a","value":true}]}', 'x.json'))
      .toThrow(ChartSpecError);
    expect(() => parseRenderSpec('{"chart":"pie","series":[{"label":1,"value":2}]}', 'x.json'))
      .toThrow(ChartSpecError);
  });

  it('非法 JSON 文本显式抛错（不是空对象冒充成功）', () => {
    expect(() => parseRenderSpec('{oops', 'x.json')).toThrow(ChartSpecError);
    expect(() => parseRenderSpec('[1,2,3]', 'x.json')).toThrow(ChartSpecError);
  });
});

describe('parseRenderSpec · CSV', () => {
  it('表头+数值列解析为 bar 数据', () => {
    const spec = parseRenderSpec('name,score\n张三,90\n李四,85.5\n', 'data/scores.csv');
    expect(spec.chart).toBe('bar');
    expect(spec.series).toEqual([
      { label: '张三', value: 90 },
      { label: '李四', value: 85.5 },
    ]);
  });

  it('非数值列 / 只有表头 / 单列 都显式抛错', () => {
    expect(() => parseRenderSpec('a,b\n1,x\n', 'x.csv')).toThrow(/不是数值/);
    expect(() => parseRenderSpec('a,b\n', 'x.csv')).toThrow(/至少需要表头行/);
    expect(() => parseRenderSpec('only\n1\n', 'x.csv')).toThrow(/至少需要两列/);
  });
});

describe('parseRenderSpec · 类型判定', () => {
  it('不支持的扩展名抛错', () => {
    expect(() => parseRenderSpec('anything', 'x.txt')).toThrow(ChartSpecError);
  });
});

describe('PreviewChart 组件（确定性 SVG）', () => {
  it('bar 渲染出 svg 与数据点数量一致的 rect', () => {
    const { container } = render(
      <PreviewChart
        spec={{ chart: 'bar', title: 'T', series: [{ label: 'a', value: 1 }, { label: 'b', value: 3 }] }}
      />,
    );
    expect(screen.getByTestId('wb-preview-chart-svg')).toBeInTheDocument();
    expect(container.querySelectorAll('rect')).toHaveLength(2);
    expect(screen.getByTestId('wb-preview-chart-meta').textContent).toContain('bar · 2 点');
  });

  it('line 与 pie 各渲染出对应几何元素', () => {
    const series = [{ label: 'a', value: 1 }, { label: 'b', value: 2 }, { label: 'c', value: 3 }];
    const { unmount, container: lineContainer } = render(<PreviewChart spec={{ chart: 'line', series }} />);
    expect(screen.getByTestId('chart-line')).toBeInTheDocument();
    expect(lineContainer.querySelectorAll('circle')).toHaveLength(3);
    unmount();
    const { container: pieContainer } = render(<PreviewChart spec={{ chart: 'pie', series }} />);
    expect(screen.getByTestId('chart-pie')).toBeInTheDocument();
    expect(pieContainer.querySelectorAll('path')).toHaveLength(3);
  });

  it('svg 带可访问的 aria-label（含点数）', () => {
    render(<PreviewChart spec={{ chart: 'bar', series: [{ label: 'a', value: 1 }] }} />);
    expect(screen.getByTestId('wb-preview-chart-svg').getAttribute('aria-label')).toContain('1 个数据点');
  });
});
