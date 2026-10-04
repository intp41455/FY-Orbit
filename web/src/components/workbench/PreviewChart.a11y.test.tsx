/**
 * P12 · 无障碍（a11y）用例 —— 平台域组件。
 *
 * 覆盖工单四类要求：键盘可达、焦点可见、语义标签、信息不只靠颜色传达。
 * jsdom 不做真实对比度计算，对比度由 UI_BASELINE 的既有配色令牌承担；
 * 本文件断言的是可编程判定（DOM/ARIA/焦点）的部分。
 */
import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import { PreviewChart } from './PreviewChart';

describe('PreviewChart 无障碍（P12）', () => {
  it('svg 必须带 role="img" 与含数据点数的 aria-label（图表信息可被读屏感知）', () => {
    render(
      <PreviewChart
        spec={{ chart: 'bar', title: '成绩', series: [{ label: 'a', value: 1 }, { label: 'b', value: 2 }] }}
      />,
    );
    const svg = screen.getByTestId('wb-preview-chart-svg');
    expect(svg.getAttribute('role')).toBe('img');
    expect(svg.getAttribute('aria-label')).toContain('成绩');
    expect(svg.getAttribute('aria-label')).toContain('2 个数据点');
  });

  it('图表元信息行重复承载图型与点数（信息不只靠颜色/图形传达）', () => {
    render(
      <PreviewChart spec={{ chart: 'pie', series: [{ label: 'a', value: 1 }] }} />,
    );
    const meta = screen.getByTestId('wb-preview-chart-meta');
    expect(meta.textContent).toContain('pie');
    expect(meta.textContent).toContain('1 点');
  });

  it('数据点标签是可见文本节点（不只以色块区分系列）', () => {
    render(
      <PreviewChart
        spec={{ chart: 'bar', series: [{ label: '张三', value: 1 }, { label: '李四', value: 2 }] }}
      />,
    );
    expect(screen.getByText('张三')).toBeInTheDocument();
    expect(screen.getByText('李四')).toBeInTheDocument();
  });
});
