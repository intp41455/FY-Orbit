/**
 * P1-A 实时预览窗（右侧预览面板 · 统一预览源注册协议前端）。
 *
 * 与 P1-10 PreviewPane（编辑器草稿实时预览）并列挂在 A 布局右栏：
 * 本面板消费「已登记的预览源」——
 *  - static 源：经 fetch 取回 /preview-sources/{id}/content 的 Blob（带鉴权、
 *    可显式报错），iframe sandbox="allow-scripts" 加载；后端响应强制
 *    CSP: sandbox allow-scripts + nosniff，双保险。
 *  - process 源：直接 iframe 指向登记时返回的 127.0.0.1 回环地址。
 *
 * 热刷新：开启「自动刷新」后每 2s 轮询 /version，版本（内容 mtime+size 哈希）
 * 变化才重新取内容，iframe 因新 blob URL 自动重载。
 *
 * 诚实原则：源不存在 / 未授权 / 文件消失一律显示明确错误状态，绝不假装渲染成功。
 * Markdown v1 未渲染：直接展示原文并显著标注，不伪装成渲染结果。
 */
import { useCallback, useEffect, useRef, useState } from 'react';
import { workbenchApi, type PreviewSource } from '../../api/workbench';
import { Spinner, errorMessage } from '../ui';
import { useBase } from '../../hooks/useAutosave';

type LoadState =
  | { status: 'idle' }
  | { status: 'loading' }
  | { status: 'ready'; blobUrl: string; version: string }
  | { status: 'error'; message: string };

/** 自动刷新轮询间隔（ms）。 */
export const AUTO_REFRESH_INTERVAL_MS = 2000;

/** v1 可登记为 static 预览源的扩展名（与后端 STATIC_MEDIA_TYPES 对齐）。 */
const STATIC_PREVIEWABLE = /\.(html?|md|markdown)$/i;

export function canRegisterStaticPreview(path: string | null): boolean {
  return !!path && STATIC_PREVIEWABLE.test(path);
}

export function PreviewPanel(props: {
  /** 当前工作区；null 时面板显示引导空态。 */
  workspaceId: string | null;
  /** 左侧当前选中文件（用于「登记为预览源」一键入口）。 */
  path: string | null;
}) {
  useBase({ surface: 'web/src/components/workbench/PreviewPanel' });
  const { workspaceId, path } = props;
  const [sources, setSources] = useState<PreviewSource[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [load, setLoad] = useState<LoadState>({ status: 'idle' });
  const [autoRefresh, setAutoRefresh] = useState(true);
  const [collapsed, setCollapsed] = useState(false);
  const [registering, setRegistering] = useState(false);
  const [registerError, setRegisterError] = useState<string | null>(null);
  const [listError, setListError] = useState<string | null>(null);

  const blobUrlRef = useRef<string | null>(null);
  const loadRef = useRef<LoadState>(load);
  loadRef.current = load;

  const selected = sources.find((s) => s.id === selectedId) ?? null;

  // 工作区切换 → 重新拉取已登记源。
  useEffect(() => {
    setSources([]);
    setSelectedId(null);
    setLoad({ status: 'idle' });
    setListError(null);
    if (!workspaceId) return;
    let cancelled = false;
    workbenchApi
      .listPreviewSources(workspaceId)
      .then((r) => {
        if (cancelled) return;
        const active = r.items.filter((s) => s.state === 'active');
        setSources(active);
        // 恢复选中：优先保留原选择，否则选第一个源。
        setSelectedId((cur) => (cur && active.some((s) => s.id === cur) ? cur : active[0]?.id ?? null));
      })
      .catch((e) => {
        if (!cancelled) setListError(errorMessage(e));
      });
    return () => {
      cancelled = true;
    };
  }, [workspaceId]);

  const loadContent = useCallback(async (src: PreviewSource) => {
    if (src.kind !== 'static') return;
    setLoad({ status: 'loading' });
    try {
      const { blob, version } = await workbenchApi.fetchPreviewSourceContent(src.id);
      const url = URL.createObjectURL(blob);
      if (blobUrlRef.current) URL.revokeObjectURL(blobUrlRef.current);
      blobUrlRef.current = url;
      setLoad({ status: 'ready', blobUrl: url, version });
    } catch (e) {
      setLoad({ status: 'error', message: errorMessage(e) });
    }
  }, []);

  // 选中 static 源变化 → 加载内容。
  useEffect(() => {
    if (selected && selected.kind === 'static') void loadContent(selected);
    if (!selected) setLoad({ status: 'idle' });
  }, [selected, loadContent]);

  // 自动刷新：仅 static 源轮询 version，变化才重取内容（新 blob URL 触发 iframe 重载）。
  useEffect(() => {
    if (!autoRefresh || collapsed || !selected || selected.kind !== 'static') return;
    const timer = window.setInterval(() => {
      const cur = loadRef.current;
      if (cur.status !== 'ready') return;
      workbenchApi
        .previewSourceVersion(selected.id)
        .then((v) => {
          const now = loadRef.current;
          if (v.version !== cur.version && now.status === 'ready') {
            void loadContent(selected);
          }
        })
        .catch(() => {
          /* 瞬时网络抖动不终止轮询；内容端点会给出明确错误 */
        });
    }, AUTO_REFRESH_INTERVAL_MS);
    return () => window.clearInterval(timer);
  }, [autoRefresh, collapsed, selected, loadContent]);

  useEffect(
    () => () => {
      if (blobUrlRef.current) URL.revokeObjectURL(blobUrlRef.current);
    },
    [],
  );

  const canRegister = canRegisterStaticPreview(path);

  async function registerCurrentFile() {
    if (!workspaceId || !path) return;
    setRegistering(true);
    setRegisterError(null);
    try {
      const src = await workbenchApi.registerStaticPreviewSource(workspaceId, path);
      const r = await workbenchApi.listPreviewSources(workspaceId);
      setSources(r.items.filter((s) => s.state === 'active'));
      setSelectedId(src.id);
    } catch (e) {
      setRegisterError(errorMessage(e));
    } finally {
      setRegistering(false);
    }
  }

  async function unregisterSelected() {
    if (!workspaceId || !selected) return;
    try {
      await workbenchApi.unregisterPreviewSource(selected.id);
      const r = await workbenchApi.listPreviewSources(workspaceId);
      const active = r.items.filter((s) => s.state === 'active');
      setSources(active);
      setSelectedId((cur) => (cur && active.some((s) => s.id === cur) ? cur : active[0]?.id ?? null));
    } catch (e) {
      setRegisterError(errorMessage(e));
    }
  }

  function manualRefresh() {
    if (selected?.kind === 'static') void loadContent(selected);
    else setLoad((l) => (l.status === 'ready' ? { ...l } : l)); // process 源由 iframe 自行加载
  }

  let body: React.ReactNode;
  if (!workspaceId) {
    body = (
      <div className="muted small" data-testid="wb-psrc-empty">
        预览窗：注册并选择工作区后，可将 .html/.md 文件登记为预览源，或登记进程预览。
      </div>
    );
  } else if (!selected) {
    body = (
      <div className="muted small" data-testid="wb-psrc-no-source">
        {listError ? (
          <span className="error-text">{listError}</span>
        ) : (
          '尚未登记预览源。左侧选中 .html/.md 文件后点击「登记为预览源」。'
        )}
      </div>
    );
  } else if (selected.kind === 'process') {
    body = (
      <div className="preview-source-process" data-testid="wb-psrc-process">
        <div className="muted small">
          进程预览 · {selected.url ?? '地址不可用'}
          <span className="badge" style={{ marginLeft: '0.4rem' }}>
            回环隔离 · 不继承核心会话
          </span>
        </div>
        {selected.url ? (
          <iframe
            key={selected.id}
            data-testid="wb-psrc-frame"
            title="进程预览"
            sandbox="allow-scripts"
            src={selected.url}
            className="preview-html-frame"
          />
        ) : (
          <div className="error-text" role="alert">进程预览地址不可用。</div>
        )}
      </div>
    );
  } else if (load.status === 'loading' || load.status === 'idle') {
    body = <Spinner label="加载预览内容…" />;
  } else if (load.status === 'error') {
    body = (
      <div className="error-text" role="alert" data-testid="wb-psrc-error">
        预览不可用：{load.message}
      </div>
    );
  } else {
    body = (
      <>
        {selected.media_type === 'text/plain' && (
          <div className="badge" data-testid="wb-psrc-md-note">
            Markdown 暂未渲染 · 以下为原文（诚实模式）
          </div>
        )}
        <iframe
          key={load.blobUrl}
          data-testid="wb-psrc-frame"
          title="预览源内容"
          // 仅授予脚本能力；不含 allow-same-origin → 文档处于唯一源，
          // 无法读取应用 cookie/DOM。后端另加 CSP: sandbox 双保险。
          sandbox="allow-scripts"
          src={load.blobUrl}
          className="preview-html-frame"
        />
      </>
    );
  }

  return (
    <div className="card preview-panel" data-testid="wb-preview-panel">
      <div className="row spread">
        <strong>实时预览窗（预览源）</strong>
        <button
          type="button"
          className="small"
          data-testid="wb-psrc-collapse"
          aria-expanded={!collapsed}
          onClick={() => setCollapsed((c) => !c)}
        >
          {collapsed ? '展开 ▲' : '折叠 ▼'}
        </button>
      </div>

      {!collapsed && (
        <>
          <div className="row spread" style={{ gap: '0.4rem', flexWrap: 'wrap' }}>
            <select
              aria-label="选择预览源"
              data-testid="wb-psrc-select"
              value={selectedId ?? ''}
              onChange={(e) => setSelectedId(e.target.value || null)}
              disabled={sources.length === 0}
            >
              {sources.length === 0 && <option value="">（无已登记源）</option>}
              {sources.map((s) => (
                <option key={s.id} value={s.id}>
                  {s.kind === 'static' ? `静态 · ${s.path}` : `进程 · ${s.preview_session_id ?? s.id}`}
                </option>
              ))}
            </select>
            <span style={{ display: 'flex', gap: '0.4rem', alignItems: 'center' }}>
              <button type="button" className="small" data-testid="wb-psrc-refresh" onClick={manualRefresh}>
                刷新
              </button>
              <label className="small" style={{ display: 'flex', gap: '0.2rem', alignItems: 'center' }}>
                <input
                  type="checkbox"
                  data-testid="wb-psrc-auto"
                  checked={autoRefresh}
                  onChange={(e) => setAutoRefresh(e.target.checked)}
                />
                自动刷新
              </label>
              {selected && (
                <button type="button" className="small danger" data-testid="wb-psrc-offline" onClick={() => void unregisterSelected()}>
                  下线
                </button>
              )}
            </span>
          </div>

          <div className="row spread" style={{ marginTop: '0.4rem' }}>
            {canRegister ? (
              <button
                type="button"
                className="primary small"
                data-testid="wb-psrc-register"
                disabled={registering || !workspaceId}
                onClick={() => void registerCurrentFile()}
              >
                {registering ? '登记中…' : `登记为预览源：${path}`}
              </button>
            ) : (
              <span className="muted small" data-testid="wb-psrc-register-hint">
                选中 .html/.md 文件后可一键登记为预览源
              </span>
            )}
          </div>
          {registerError && (
            <div className="error-text" role="alert" data-testid="wb-psrc-register-error">
              {registerError}
            </div>
          )}

          <div className="preview-body">{body}</div>
        </>
      )}
    </div>
  );
}
