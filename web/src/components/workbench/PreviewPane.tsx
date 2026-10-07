/**
 * 预览窗（A 布局右栏，P1-02 挂载点）— P1-10 实时预览实装。
 *
 * 数据流：CodeEditor 在文件加载/编辑/清空时经 previewBus 发布实时草稿 →
 * 本面板在可配置节流窗口（300/400/500ms，防抖式：连续输入只取最新值）内
 * 合并变更并刷新预览；角标全程显示「待预览 / 节流中 / 已刷新」状态。
 *
 * 预览类型（按扩展名判定）：
 *  - .md/.markdown → 轻量 Markdown 渲染（全量转义，见 previewMarkdown.ts，XSS 安全）；
 *  - .html/.htm   → iframe sandbox="" 沙箱预览（srcDoc 注入；沙箱不授予
 *    allow-scripts/allow-same-origin，编辑中的脚本完全禁用，无法逃逸）；
 *  - 其他文本      → 纯文本兜底展示。
 */
import { useEffect, useRef, useState } from 'react';
import { subscribePreviewDraft, type PreviewDraft } from './previewBus';
import { renderMarkdown } from './previewMarkdown';
import { parseRenderSpec, PreviewChart } from './PreviewChart';
import type { DispatchContext } from './DispatchDialog';
import { useBase } from '../../hooks/useAutosave';

type PreviewKind = 'markdown' | 'html' | 'text' | 'data';

type Status = 'idle' | 'throttling' | 'refreshed';

/** 节流窗口可配范围（工单口径 300-500ms）。 */
const THROTTLE_OPTIONS = [300, 400, 500] as const;
const DEFAULT_THROTTLE_MS = 400;

function detectKind(path: string | null): PreviewKind {
  if (!path) return 'text';
  const lower = path.toLowerCase();
  if (lower.endsWith('.md') || lower.endsWith('.markdown')) return 'markdown';
  if (lower.endsWith('.html') || lower.endsWith('.htm')) return 'html';
  // P2 · Layer 4：结构化数据文件 → 图表渲染（不可渲染时显式失败）。
  if (lower.endsWith('.json') || lower.endsWith('.csv')) return 'data';
  return 'text';
}

/** P1-15 派改入口的可点击元素（内容 + 展示名）。 */
const DISPATCH_ELEMENTS: { label: string; text: string }[] = [
  {
    label: '产品简介段落',
    text:
      '多智能体搭建台（Find Yourself）是一款本地多智能体搭建与调度平台：' +
      '可视化搭建、代码级自定义，无需注册联网；全部数据来自已鉴权的后端 API。',
  },
  {
    label: '工作台说明段落',
    text:
      '工程代码工作台提供文件树、编辑器、终端与 git 面板；预览窗实时渲染' +
      'Markdown 与沙箱化 HTML，选中内容可一键发起派改（统一走 ModelBinding 链路）。',
  },
];

export function PreviewPane(props: {
  // ---- P1-15 追加（点击弹窗派改）：可选注入，不影响 P1-10 既有行为 ----
  /** 点击派改元素回调：弹出派改弹窗 */
  onElementDispatch?: (ctx: DispatchContext) => void;
  /** 派改结果回填预览展示 */
  dispatchResult?: string | null;
}) {
  useBase({ surface: 'web/src/components/workbench/PreviewPane' });
  const { onElementDispatch, dispatchResult } = props;
  const [draft, setDraft] = useState<PreviewDraft | null>(null);
  // 节流窗口结束后真正上屏的快照。
  const [rendered, setRendered] = useState<PreviewDraft | null>(null);
  const [status, setStatus] = useState<Status>('idle');
  const [throttleMs, setThrottleMs] = useState<number>(DEFAULT_THROTTLE_MS);
  const [refreshedAt, setRefreshedAt] = useState<string | null>(null);
  const timerRef = useRef<number | null>(null);

  useEffect(() => {
    const unsubscribe = subscribePreviewDraft((d) => {
      setDraft(d);
    });
    return unsubscribe;
  }, []);

  const pending = draft !== null && (rendered === null || draft.path !== rendered.path || draft.content !== rendered.content);

  useEffect(() => {
    if (!pending) return;
    setStatus('throttling');
    timerRef.current = window.setTimeout(() => {
      setRendered(draft);
      setStatus('refreshed');
      setRefreshedAt(new Date().toLocaleTimeString('zh-CN', { hour12: false }));
    }, throttleMs);
    return () => {
      if (timerRef.current !== null) {
        window.clearTimeout(timerRef.current);
        timerRef.current = null;
      }
    };
  }, [pending, draft, throttleMs]);

  const kind = detectKind(rendered?.path ?? null);
  const statusLabel =
    status === 'throttling'
      ? `节流中 ${throttleMs}ms`
      : status === 'refreshed'
        ? `已刷新 ${refreshedAt ?? ''}`
        : '待预览';

  let body: React.ReactNode;
  if (!rendered || rendered.path === null) {
    body = (
      <div className="muted small" data-testid="wb-preview-empty">
        预览内容区：从左侧文件树选择一个 Markdown / HTML 文件即可实时预览。
      </div>
    );
  } else if (kind === 'markdown') {
    body = (
      <div
        className="preview-markdown"
        data-testid="wb-preview-markdown"
        // 输入已全量转义 + 白名单标签，见 previewMarkdown.ts 头注。
        dangerouslySetInnerHTML={{ __html: renderMarkdown(rendered.content) }}
      />
    );
  } else if (kind === 'html') {
    body = (
      <iframe
        data-testid="wb-preview-html-frame"
        title="HTML 沙箱预览"
        // sandbox 为空串：不授予任何能力（无脚本/无同源/无表单），
        // 编辑中的 HTML 即使内嵌 <script> 也无法执行或逃逸。
        sandbox=""
        srcDoc={rendered.content}
        className="preview-html-frame"
      />
    );
  } else if (kind === 'data') {
    // P2 · Layer 4：结构化数据 → 图表。不可渲染**显式失败**（错误面板），
    // 绝不回落空白预览冒充成功（铁律 1）。
    let spec: ReturnType<typeof parseRenderSpec> | null = null;
    let chartError: string | null = null;
    try {
      spec = parseRenderSpec(rendered.content, rendered.path);
    } catch (e) {
      chartError = e instanceof Error ? e.message : String(e);
    }
    body = spec ? (
      <PreviewChart spec={spec} />
    ) : (
      <div
        className="error-text"
        role="alert"
        data-testid="wb-preview-chart-error"
      >
        图表渲染失败：{chartError}
      </div>
    );
  } else {
    body = (
      <pre className="preview-text" data-testid="wb-preview-text">
        {rendered.content}
      </pre>
    );
  }

  return (
    <div className="card preview-pane" data-testid="wb-preview-pane">
      <div className="row spread">
        <strong>预览窗</strong>
        <span style={{ display: 'flex', gap: '0.4rem', alignItems: 'center' }}>
          <select
            aria-label="节流窗口"
            data-testid="wb-preview-throttle"
            value={throttleMs}
            onChange={(e) => setThrottleMs(Number(e.target.value))}
          >
            {THROTTLE_OPTIONS.map((ms) => (
              <option key={ms} value={ms}>
                {ms}ms
              </option>
            ))}
          </select>
          <span
            className="badge"
            data-testid="wb-preview-status"
            data-state={status}
            title={`节流窗口 ${throttleMs}ms（300-500ms 可配）`}
          >
            {statusLabel}
          </span>
        </span>
      </div>
      {rendered?.path && (
        <div className="muted small" data-testid="wb-preview-path" title={rendered.path}>
          {rendered.path} · {kind === 'markdown' ? 'Markdown' : kind === 'html' ? 'HTML 沙箱' : kind === 'data' ? '图表数据' : '纯文本'}
        </div>
      )}
      <div className="preview-body">{body}</div>
      {/* ---- P1-15 追加：派改入口区（点击元素→派改弹窗） + 结果回填区 ---- */}
      {onElementDispatch && (
        <div data-testid="wb-preview-dispatch-zone" style={{ marginTop: '0.6rem' }}>
          <div className="muted small">派改：点击下方元素可发起改写（统一走 ModelBinding 链路）</div>
          {DISPATCH_ELEMENTS.map((el) => (
            <button
              key={el.label}
              type="button"
              className="preview-dispatch-element"
              data-testid="preview-element"
              onClick={() =>
                onElementDispatch({ source: `预览窗 · ${el.label}`, text: el.text })
              }
              style={{ display: 'block', width: '100%', textAlign: 'left', margin: '0.3rem 0' }}
            >
              <span className="badge">{el.label}</span>
              <span className="small" style={{ display: 'block', marginTop: '0.2rem' }}>{el.text}</span>
            </button>
          ))}
        </div>
      )}
      {dispatchResult != null && dispatchResult !== '' && (
        <div data-testid="preview-dispatch-result" style={{ marginTop: '0.5rem' }}>
          <div className="muted small">派改结果回填</div>
          <pre className="small" style={{ whiteSpace: 'pre-wrap', margin: '0.2rem 0 0' }}>
            {dispatchResult}
          </pre>
        </div>
      )}
    </div>
  );
}
