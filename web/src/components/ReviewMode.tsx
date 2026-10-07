import { useCallback, useEffect, useRef, useState } from 'react';
import {
  addReviewNote,
  deleteReviewNote,
  exportReviewNotes,
  listReviewNotes,
  openReviewSession,
  patchReviewNote,
  type ReviewNoteView,
} from '../api/review';
import { AnnotationCanvas } from './review/AnnotationCanvas';
import { RegionSelectOverlay } from './review/RegionSelectOverlay';
import { RefreshLoopBar, useRefreshLoop } from './review/RefreshLoop';
import { ReviewNoteList } from './review/ReviewNoteList';
import {
  REVIEW_UI_ATTR,
  captureDomTarget,
  currentViewport,
  isInsideReviewUi,
  type NormalizedRegion,
  type Stroke,
} from './review';
import { useBase } from '../hooks/useAutosave';

/**
 * UI 评审模式（Design Review Mode）· P9 进阶版
 *
 * 右下角悬浮按钮开关。开启后三种玩法：
 * - **点选**（原有）：悬停高亮任意组件，点击即在右侧生成意见；
 * - **圈选**（A-点哪评哪-06）：拖拽框出一片区域（归一化坐标，跨分辨率可复原）；
 * - **批注**（A-点哪评哪-07）：画笔自由画线 + 录音，落成矢量笔迹与语音引用。
 *
 * 与初版的区别（P9 升级）：
 * - 意见**落库**（`/api/review`）而非只存 localStorage——换设备不丢、执行
 *   Agent 能拿到；localStorage 降级为**离线缓存**，服务端不可达时显式提示；
 * - 承载 TOKEN 优化三项：真写标记 / 短码 / 结构差分（`changed` 由后端算）；
 * - 内建热刷新闭环状态条（A-点哪评哪-05）。
 *
 * 评审 UI 自身以 data-review-ui 标记，不会误伤正常点击。
 */

interface ReviewNote {
  id: string;
  ts: string;
  page: string;
  target: {
    tag: string;
    id: string;
    cls: string;
    text: string;
    selector: string;
  };
  note: string;
}

const STORAGE_KEY = 'fy-review-notes';

/** 服务端记录缺失时，降级用的本地草稿（离线缓存，非权威）。 */
function loadLocalDrafts(): ReviewNote[] {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return [];
    const parsed = JSON.parse(raw) as ReviewNote[];
    return Array.isArray(parsed) ? parsed : [];
  } catch {
    return [];
  }
}

export function ReviewMode() {
  useBase({ surface: 'web/src/components/ReviewMode' });
  const [active, setActive] = useState(false);
  const [mode, setMode] = useState<'select' | 'region' | 'annotate'>('select');
  /** 服务端（权威）意见 */
  const [serverNotes, setServerNotes] = useState<ReviewNoteView[]>([]);
  /** 本地草稿（服务端不可达时的离线缓存） */
  const [drafts, setDrafts] = useState<ReviewNote[]>(loadLocalDrafts);
  const [sessionId, setSessionId] = useState('');
  const [syncError, setSyncError] = useState('');
  const hoveredRef = useRef<HTMLElement | null>(null);

  const refresh = useRefreshLoop({
    sessionId: sessionId || 'local',
    reload: false,
    onIteration: () => {
      if (sessionId) void reloadNotes(sessionId);
    },
  });

  const reloadNotes = useCallback(async (sid: string): Promise<void> => {
    try {
      const res = await listReviewNotes(sid);
      setServerNotes(res.notes);
      setSyncError('');
    } catch {
      setSyncError('服务端不可达，意见暂存本地（未同步）');
    }
  }, []);

  // 开启评审：建会话（服务端不可达则降级到本地草稿模式）
  useEffect(() => {
    if (!active || sessionId) return;
    void (async () => {
      try {
        const res = await openReviewSession({ page: window.location.pathname, route: 'whitebox' });
        setSessionId(res.session.id);
        await reloadNotes(res.session.id);
      } catch {
        setSyncError('服务端不可达，意见暂存本地（未同步）');
      }
    })();
  }, [active, sessionId, reloadNotes]);

  // 本地草稿持久化（离线缓存）
  useEffect(() => {
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(drafts));
    } catch {
      // storage full/unavailable: drafts stay in memory for this session
    }
  }, [drafts]);

  const clearHover = useCallback(() => {
    if (hoveredRef.current) {
      hoveredRef.current.classList.remove('fy-review-hover');
      hoveredRef.current = null;
    }
  }, []);

  /** 提交一条 DOM 点选意见。 */
  const submitDomNote = useCallback(
    async (el: HTMLElement): Promise<void> => {
      const cap = captureDomTarget(el);
      if (sessionId) {
        try {
          const res = await addReviewNote(sessionId, {
            page: window.location.pathname,
            mode: 'dom',
            tag: cap.tag,
            element_id: cap.element_id,
            element_class: cap.element_class,
            text: cap.text,
            selector: cap.selector,
            dom_path: cap.dom_path,
            note: '',
          });
          setServerNotes((prev) => [...prev, res.note]);
          setSyncError('');
          return;
        } catch {
          setSyncError('服务端不可达，意见暂存本地（未同步）');
        }
      }
      // 降级：本地草稿
      setDrafts((prev) => [
        ...prev,
        {
          id: `n-${Date.now()}-${Math.random().toString(36).slice(2, 7)}`,
          ts: new Date().toLocaleTimeString('zh-CN'),
          page: window.location.pathname,
          target: {
            tag: cap.tag, id: cap.element_id, cls: cap.element_class,
            text: cap.text, selector: cap.selector,
          },
          note: '',
        },
      ]);
    },
    [sessionId],
  );

  /** 提交圈选意见（A-点哪评哪-06）。 */
  const submitRegionNote = useCallback(
    async (region: NormalizedRegion): Promise<void> => {
      setMode('select');
      if (!sessionId) {
        setSyncError('服务端不可达，圈选意见暂存本地（未同步）');
        return;
      }
      try {
        const res = await addReviewNote(sessionId, {
          page: window.location.pathname,
          mode: 'region',
          region,
          note: '',
        });
        setServerNotes((prev) => [...prev, res.note]);
        setSyncError('');
      } catch {
        setSyncError('服务端不可达，圈选意见暂存本地（未同步）');
      }
    },
    [sessionId],
  );

  /** 提交画笔/语音批注（A-点哪评哪-07）。 */
  const submitAnnotation = useCallback(
    async (payload: { strokes: Stroke[]; audio_ref: string; audio_transcript: string }): Promise<void> => {
      setMode('select');
      if (!sessionId) {
        setSyncError('服务端不可达，批注暂存本地（未同步）');
        return;
      }
      try {
        const res = await addReviewNote(sessionId, {
          page: window.location.pathname,
          mode: 'freehand',
          strokes: payload.strokes,
          audio_ref: payload.audio_ref,
          audio_transcript: payload.audio_transcript,
          note: '',
        });
        setServerNotes((prev) => [...prev, res.note]);
        setSyncError('');
      } catch {
        setSyncError('服务端不可达，批注暂存本地（未同步）');
      }
    },
    [sessionId],
  );

  // 点选模式的全局事件（圈选/批注模式由各自覆盖层接管）
  useEffect(() => {
    if (!active || mode !== 'select') {
      clearHover();
      return;
    }
    const styleId = 'fy-review-hover-style';
    if (!document.getElementById(styleId)) {
      const style = document.createElement('style');
      style.id = styleId;
      style.textContent =
        '.fy-review-hover{outline:2px dashed var(--sky,#0284c7) !important;' +
        'outline-offset:2px;cursor:crosshair !important;}';
      document.head.appendChild(style);
    }

    const onOver = (e: MouseEvent): void => {
      if (isInsideReviewUi(e.target)) {
        clearHover();
        return;
      }
      if (e.target instanceof HTMLElement) {
        if (hoveredRef.current === e.target) return;
        clearHover();
        e.target.classList.add('fy-review-hover');
        hoveredRef.current = e.target;
      }
    };

    const onClick = (e: MouseEvent): void => {
      if (isInsideReviewUi(e.target)) return; // 评审面板自身正常交互
      e.preventDefault();
      e.stopPropagation();
      const el = e.target instanceof HTMLElement ? e.target : null;
      if (!el) return;
      void submitDomNote(el);
      clearHover();
    };

    const onKey = (e: KeyboardEvent): void => {
      if (e.key === 'Escape') setActive(false);
    };

    document.addEventListener('mouseover', onOver, true);
    document.addEventListener('click', onClick, true);
    document.addEventListener('keydown', onKey);
    return () => {
      document.removeEventListener('mouseover', onOver, true);
      document.removeEventListener('click', onClick, true);
      document.removeEventListener('keydown', onKey);
      clearHover();
    };
  }, [active, mode, submitDomNote, clearHover]);

  // -- 服务端意见操作 -------------------------------------------------------

  const updateServerNote = async (id: string, text: string): Promise<void> => {
    setServerNotes((prev) => prev.map((n) => (n.id === id ? { ...n, note: text } : n)));
    try {
      await patchReviewNote(id, { note: text });
    } catch {
      setSyncError('意见修改未能同步到服务端');
    }
  };

  const changeServerState = async (id: string, state: ReviewNoteView['state']): Promise<void> => {
    try {
      const res = await patchReviewNote(id, { state });
      setServerNotes((prev) => prev.map((n) => (n.id === id ? res.note : n)));
      if (state === 'resolved') refresh.notifyChange('意见标记已改完');
    } catch {
      setSyncError('状态修改未能同步到服务端');
    }
  };

  const removeServerNote = async (id: string): Promise<void> => {
    try {
      await deleteReviewNote(id);
      setServerNotes((prev) => prev.filter((n) => n.id !== id));
    } catch {
      setSyncError('删除未能同步到服务端');
    }
  };

  // -- 本地草稿操作（降级模式）----------------------------------------------

  const updateDraft = (id: string, text: string): void =>
    setDrafts((prev) => prev.map((n) => (n.id === id ? { ...n, note: text } : n)));
  const removeDraft = (id: string): void =>
    setDrafts((prev) => prev.filter((n) => n.id !== id));

  const totalCount = serverNotes.length + drafts.length;

  const exportNotes = async (): Promise<void> => {
    let md: string;
    if (sessionId) {
      try {
        const res = await exportReviewNotes(sessionId, false);
        md = res.markdown;
      } catch {
        md = buildLocalMarkdown(drafts);
      }
    } else {
      md = buildLocalMarkdown(drafts);
    }
    const stamp = new Date().toISOString().slice(0, 16).replace(/[-:T]/g, '');
    const blob = new Blob([md], { type: 'text/markdown;charset=utf-8' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `ui-review-${stamp}.md`;
    a.click();
    URL.revokeObjectURL(url);
    void navigator.clipboard?.writeText(md).catch(() => undefined);
  };

  return (
    <>
      {/* 发行包默认隐藏：这是开发/验收期的批注工具，不是最终用户功能。
          生产构建（绿色包内的 web/dist）不渲染；开发态与单测仍可用。 */}
      {!active && !import.meta.env.PROD && (
        <button
          type="button"
          {...{ [REVIEW_UI_ATTR]: '' }}
          data-testid="review-toggle"
          onClick={() => setActive(true)}
          style={{
            position: 'fixed', right: 18, bottom: 18, zIndex: 2147483000,
            padding: '10px 14px', borderRadius: '999px', border: '1px solid var(--glass-border)',
            background: 'var(--glass-strong)', color: 'var(--sky-deep)',
            boxShadow: 'var(--shadow)', cursor: 'pointer', fontSize: 13, fontWeight: 600,
          }}
          title="开启点哪评哪：点选 / 圈选 / 画笔批注"
        >
          🔍 评审
        </button>
      )}

      {active && mode === 'region' && (
        <RegionSelectOverlay onSelect={submitRegionNote} onCancel={() => setMode('select')} />
      )}
      {active && mode === 'annotate' && (
        <AnnotationCanvas onSubmit={submitAnnotation} onCancel={() => setMode('select')} />
      )}

      {active && (
        <aside
          {...{ [REVIEW_UI_ATTR]: '' }}
          data-testid="review-panel"
          style={{
            position: 'fixed', top: 0, right: 0, height: '100vh', width: 360, zIndex: 2147483000,
            background: 'var(--glass-elevated)', borderLeft: '1px solid var(--line)',
            boxShadow: 'var(--shadow)', display: 'flex', flexDirection: 'column',
            fontFamily: 'inherit',
          }}
        >
          <div style={{ padding: '14px 16px', borderBottom: '1px solid var(--line)' }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
              <strong style={{ color: 'var(--text-main)' }}>点哪评哪</strong>
              <span style={{ color: 'var(--text-muted)', fontSize: 12 }}>
                {mode === 'select' ? '点击 / 圈选 / 批注（Esc 退出）' : '按住拖拽以完成'}
              </span>
            </div>

            <div style={{ display: 'flex', gap: 6, marginTop: 10 }}>
              <ModeChip label="点选" active={mode === 'select'} testid="mode-select"
                onClick={() => setMode('select')} />
              <ModeChip label="圈选" active={mode === 'region'} testid="mode-region"
                onClick={() => setMode('region')} />
              <ModeChip label="批注" active={mode === 'annotate'} testid="mode-annotate"
                onClick={() => setMode('annotate')} />
            </div>

            <div style={{ display: 'flex', gap: 8, marginTop: 10 }}>
              <button data-testid="review-export" type="button" onClick={() => void exportNotes()}
                disabled={totalCount === 0}
                style={{ flex: 1, padding: '7px 0', borderRadius: 10, border: '1px solid var(--ice-soft)',
                  background: 'var(--sky)', color: '#fff',
                  cursor: totalCount ? 'pointer' : 'not-allowed', fontSize: 13, opacity: totalCount ? 1 : 0.6 }}>
                导出意见（{totalCount}）
              </button>
              <button data-testid="review-clear" type="button"
                onClick={() => { if (confirm('清空全部本地草稿？服务端意见不受影响')) setDrafts([]); }}
                style={{ padding: '7px 10px', borderRadius: 10, border: '1px solid var(--amber-border)',
                  background: 'var(--amber-bg)', color: 'var(--amber)', cursor: 'pointer', fontSize: 13 }}>
                清空草稿
              </button>
              <button data-testid="review-done" type="button" onClick={() => setActive(false)}
                style={{ padding: '7px 10px', borderRadius: 10, border: '1px solid var(--line)',
                  background: 'var(--glass-base)', color: 'var(--text-muted)', cursor: 'pointer', fontSize: 13 }}>
                完成
              </button>
            </div>

            {syncError && (
              <div data-testid="review-sync-error"
                style={{ marginTop: 8, fontSize: 11, color: 'var(--amber)' }}>
                {syncError}
              </div>
            )}
          </div>

          <RefreshLoopBar
            refreshState={refresh.refreshState}
            refreshNote={refresh.refreshNote}
            iteration={refresh.iteration}
            onManualRefresh={() => refresh.notifyChange('手动触发刷新')}
          />

          <div style={{ flex: 1, overflowY: 'auto', padding: 12, display: 'flex', flexDirection: 'column', gap: 10 }}>
            <ReviewNoteList
              notes={serverNotes}
              onNoteChange={(id, t) => void updateServerNote(id, t)}
              onStateChange={(id, s) => void changeServerState(id, s)}
              onDelete={(id) => void removeServerNote(id)}
            />

            {drafts.length > 0 && (
              <div data-testid="review-drafts" style={{ borderTop: '1px dashed var(--line)', paddingTop: 8 }}>
                <div style={{ fontSize: 11, color: 'var(--amber)', marginBottom: 6 }}>
                  本地草稿（未同步）
                </div>
                {drafts.map((n) => (
                  <div key={n.id}
                    style={{ border: '1px solid var(--line)', borderRadius: 12, padding: 10,
                      background: 'var(--glass-base)', marginBottom: 8 }}>
                    <div style={{ fontSize: 12, color: 'var(--sky-deep)', fontWeight: 600, wordBreak: 'break-all' }}>
                      {n.page} · &lt;{n.target.tag}&gt;
                      {n.target.id && ` #${n.target.id}`}
                      {n.target.cls && ` .${n.target.cls.split(' ').join('.')}`}
                    </div>
                    <textarea
                      value={n.note}
                      onChange={(e) => updateDraft(n.id, e.target.value)}
                      placeholder="这里写修改意见…"
                      style={{ width: '100%', minHeight: 52, marginTop: 6, borderRadius: 8,
                        border: '1px solid var(--line)', padding: 8, fontSize: 13,
                        background: '#fff', color: 'var(--text-main)', resize: 'vertical', boxSizing: 'border-box' }}
                    />
                    <div style={{ display: 'flex', justifyContent: 'space-between', marginTop: 6 }}>
                      <span style={{ fontSize: 11, color: 'var(--text-faint)' }}>{n.ts} · {n.target.selector}</span>
                      <button type="button" onClick={() => removeDraft(n.id)}
                        style={{ fontSize: 12, color: 'var(--rose)', background: 'none',
                          border: 'none', cursor: 'pointer' }}>
                        删除
                      </button>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>
        </aside>
      )}
    </>
  );
}

function ModeChip({ label, active, onClick, testid }: {
  label: string; active: boolean; onClick: () => void; testid: string;
}) {
  return (
    <button data-testid={testid} type="button" onClick={onClick}
      style={{
        flex: 1, padding: '5px 0', borderRadius: 8, fontSize: 12, cursor: 'pointer',
        border: '1px solid var(--line)',
        background: active ? 'var(--sky)' : 'var(--glass-base)',
        color: active ? '#fff' : 'var(--text-main)',
        fontWeight: active ? 600 : 400,
      }}>
      {label}
    </button>
  );
}

/** 降级导出：仅本地草稿（服务端不可达时）。 */
function buildLocalMarkdown(notes: ReviewNote[]): string {
  if (notes.length === 0) return '# UI 评审意见\n\n（无意见）';
  const lines = [`# UI 评审意见（本地草稿，未同步）`, '', `共 ${notes.length} 条。`, ''];
  notes.forEach((n, i) => {
    lines.push(
      `### ${i + 1}. <${n.target.tag}>${n.target.id ? ` #${n.target.id}` : ''}`,
      `- 页面：${n.page}`,
      `- 选择器：\`${n.target.selector}\``,
      `- 意见：${n.note.trim() || '（未填写）'}`,
      '',
    );
  });
  return lines.join('\n');
}

//: 视口工具透出（供测试与后续扩展复用）
export { currentViewport };
