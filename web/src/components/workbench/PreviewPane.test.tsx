import { render, screen, act, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi, afterEach } from 'vitest';
import { PreviewPane } from './PreviewPane';
import { publishPreviewDraft } from './previewBus';

describe('P1-10 PreviewPane 实时预览', () => {
  afterEach(() => {
    vi.useRealTimers();
  });

  it('Markdown 草稿在节流窗口后渲染（未保存内容也可预览）', async () => {
    render(<PreviewPane />);
    // 空态
    expect(screen.getByTestId('wb-preview-empty')).toBeInTheDocument();

    act(() => {
      publishPreviewDraft({ path: 'notes/demo.md', content: '# 标题一\n\n**加粗** 文本' });
    });
    // 节流窗口内：状态为「节流中」，内容尚未上屏
    expect(screen.getByTestId('wb-preview-status')).toHaveAttribute('data-state', 'throttling');
    expect(screen.queryByTestId('wb-preview-markdown')).not.toBeInTheDocument();

    // 节流窗口后：渲染出现（默认 400ms 窗口）
    const md = await screen.findByTestId('wb-preview-markdown', {}, { timeout: 1200 });
    expect(md).toContainHTML('<h1>标题一</h1>');
    expect(md).toContainHTML('<strong>加粗</strong>');
    expect(screen.getByTestId('wb-preview-status')).toHaveAttribute('data-state', 'refreshed');
    expect(screen.getByTestId('wb-preview-status')).toHaveTextContent(/已刷新/);
  });

  it('Markdown 中的脚本与事件属性被转义（XSS 防护）', async () => {
    render(<PreviewPane />);
    act(() => {
      publishPreviewDraft({
        path: 'xss.md',
        content: '<script>window.__pwned=1</script>\n\n<img src=x onerror="alert(1)">\n\n[javascript: 链接](javascript:alert(2))',
      });
    });
    const md = await screen.findByTestId('wb-preview-markdown', {}, { timeout: 1200 });
    // 不存在任何可执行节点
    expect(md.querySelector('script')).toBeNull();
    expect(md.querySelector('img[onerror]')).toBeNull();
    expect(md.querySelector('a[href^="javascript:"]')).toBeNull();
    // 原文以转义文本形式可见
    expect(md).toHaveTextContent('<script>window.__pwned=1</script>');
  });

  it('HTML 文件走 iframe 沙箱：sandbox 不授予脚本能力，内容经 srcDoc 注入', async () => {
    render(<PreviewPane />);
    act(() => {
      publishPreviewDraft({ path: 'page.html', content: '<h1>沙箱标题</h1><script>alert(1)</script>' });
    });
    const frame = await screen.findByTestId('wb-preview-html-frame', {}, { timeout: 1200 });
    // sandbox="" — 无 allow-scripts / allow-same-origin，脚本完全禁用
    expect(frame).toHaveAttribute('sandbox', '');
    expect(frame.getAttribute('srcdoc')).toContain('沙箱标题');
    // 危险文本不会绕过沙箱出现在主文档
    expect(document.querySelector('main script')).toBeNull();
  });

  it('节流窗口可通过面板选择器调整（300-500ms 可配）', async () => {
    const user = userEvent.setup();
    render(<PreviewPane />);
    const select = screen.getByTestId('wb-preview-throttle');
    expect(select).toHaveValue('400');
    await user.selectOptions(select, '300');
    expect(select).toHaveValue('300');

    act(() => {
      publishPreviewDraft({ path: 'a.md', content: '# A' });
    });
    await waitFor(
      () => expect(screen.getByTestId('wb-preview-status')).toHaveAttribute('data-state', 'refreshed'),
      { timeout: 1000 },
    );
  });

  it('连续输入合并为一次刷新（防抖式节流）', async () => {
    vi.useFakeTimers();
    render(<PreviewPane />);
    act(() => {
      publishPreviewDraft({ path: 'b.md', content: '# 第一版' });
    });
    // 窗口内继续输入 → 计时器重置
    act(() => {
      vi.advanceTimersByTime(300);
      publishPreviewDraft({ path: 'b.md', content: '# 第二版' });
    });
    act(() => {
      vi.advanceTimersByTime(300);
    });
    // 仍在窗口内（重置后 300 < 400），未上屏
    expect(screen.queryByTestId('wb-preview-markdown')).not.toBeInTheDocument();
    act(() => {
      vi.advanceTimersByTime(150);
    });
    const md = screen.getByTestId('wb-preview-markdown');
    expect(md).toContainHTML('<h1>第二版</h1>');
    expect(md).not.toContainHTML('<h1>第一版</h1>');
  });

  it('未选择文件时回到空态', async () => {
    render(<PreviewPane />);
    act(() => {
      publishPreviewDraft({ path: 'c.md', content: '# C' });
    });
    await screen.findByTestId('wb-preview-markdown', {}, { timeout: 1200 });
    act(() => {
      publishPreviewDraft({ path: null, content: '' });
    });
    await waitFor(() => expect(screen.getByTestId('wb-preview-empty')).toBeInTheDocument());
  });
});

// ---------------------------------------------------------------------------
// P2 · Layer 4 图表渲染（结构化数据 → SVG 图表；不可渲染显式失败）
// ---------------------------------------------------------------------------

describe('P2 PreviewPane 图表数据预览', () => {
  it('.json 图表数据在节流后渲染为 SVG 图表', async () => {
    const { container } = render(<PreviewPane />);
    act(() => {
      publishPreviewDraft({
        path: 'data/scores.json',
        content: '{"chart":"bar","title":"成绩","series":[{"label":"张三","value":90},{"label":"李四","value":85}]}',
      });
    });
    const chart = await screen.findByTestId('wb-preview-chart', {}, { timeout: 1200 });
    expect(chart).toBeInTheDocument();
    expect(screen.getByTestId('wb-preview-chart-svg')).toBeInTheDocument();
    expect(container.querySelectorAll('rect')).toHaveLength(2);
    expect(screen.getByTestId('wb-preview-path').textContent).toContain('图表数据');
  });

  it('.csv 数据渲染为默认 bar 图表', async () => {
    const { container } = render(<PreviewPane />);
    act(() => {
      publishPreviewDraft({ path: 'data/rain.csv', content: 'month,mm\n1月,12\n2月,30\n' });
    });
    await screen.findByTestId('wb-preview-chart', {}, { timeout: 1200 });
    expect(container.querySelectorAll('rect')).toHaveLength(2);
  });

  it('不可渲染的 JSON → 显式错误面板（role=alert），绝不空白冒充成功', async () => {
    render(<PreviewPane />);
    act(() => {
      publishPreviewDraft({ path: 'data/broken.json', content: '{"chart":"bar","series":[]}' });
    });
    const err = await screen.findByTestId('wb-preview-chart-error', {}, { timeout: 1200 });
    expect(err).toHaveAttribute('role', 'alert');
    expect(err.textContent).toContain('图表渲染失败');
    expect(err.textContent).toContain('series');
    // 同屏不允许出现「成功」的空图表
    expect(screen.queryByTestId('wb-preview-chart')).not.toBeInTheDocument();
  });

  it('完全不是图表数据的 JSON（如普通配置）也显式失败并给出原因', async () => {
    render(<PreviewPane />);
    act(() => {
      publishPreviewDraft({ path: 'data/config.json', content: '{"theme":"dark"}' });
    });
    const err = await screen.findByTestId('wb-preview-chart-error', {}, { timeout: 1200 });
    expect(err.textContent).toContain('图表渲染失败');
    expect(err.textContent).toContain('chart');
  });
});
