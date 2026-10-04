import { useCallback, useEffect, useRef, useState } from 'react';

/**
 * UI 评审模式（Design Review Mode）
 *
 * 右下角悬浮按钮开关。开启后：
 * - 悬停高亮任意组件，点击即在右侧抽屉生成一条评论（自动记录页面、元素描述与选择器）；
 * - 评论实时存 localStorage，刷新不丢；
 * - 「导出」生成 Markdown 下载并复制到剪贴板，直接粘贴给执行 Agent 照单修改。
 *
 * 评审 UI 自身以 data-review-ui 标记，不会误伤正常点击。
 */

interface ReviewTarget {
  tag: string;
  id: string;
  cls: string;
  text: string;
  selector: string;
}

interface ReviewNote {
  id: string;
  ts: string;
  page: string;
  target: ReviewTarget;
  note: string;
}

const STORAGE_KEY = 'fy-review-notes';
const REVIEW_UI_ATTR = 'data-review-ui';

function loadNotes(): ReviewNote[] {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return [];
    const parsed = JSON.parse(raw) as ReviewNote[];
    return Array.isArray(parsed) ? parsed : [];
  } catch {
    return [];
  }
}

function describeTarget(el: HTMLElement): ReviewTarget {
  const parts: string[] = [];
  let node: HTMLElement | null = el;
  let depth = 0;
  while (node && node !== document.body && depth < 6) {
    let seg = node.tagName.toLowerCase();
    if (node.id) {
      seg += `#${node.id}`;
    } else {
      const parent = node.parentElement;
      if (parent) {
        const same = Array.from(parent.children).filter(
          (c) => c.tagName === node?.tagName,
        );
        if (same.length > 1) seg += `:nth-of-type(${same.indexOf(node) + 1})`;
      }
    }
    parts.unshift(seg);
    node = node.parentElement;
    depth += 1;
  }
  const cls = typeof el.className === 'string' ? el.className : '';
  return {
    tag: el.tagName.toLowerCase(),
    id: el.id || '',
    cls: cls.split(/\s+/).filter(Boolean).slice(0, 3).join(' '),
    text: (el.textContent || '').trim().replace(/\s+/g, ' ').slice(0, 60),
    selector: parts.join(' > '),
  };
}

function buildMarkdown(notes: ReviewNote[]): string {
  const byPage = new Map<string, ReviewNote[]>();
  for (const n of notes) {
    const list = byPage.get(n.page) || [];
    list.push(n);
    byPage.set(n.page, list);
  }
  const lines: string[] = [
    `# UI 评审意见（${new Date().toLocaleString('zh-CN')}）`,
    '',
    `共 ${notes.length} 条。以下每条含「页面 / 元素 / 选择器 / 意见」，执行 Agent 按此定位组件修改。`,
    '',
  ];
  let i = 0;
  for (const [page, list] of byPage) {
    lines.push(`## 页面：${page}`, '');
    for (const n of list) {
      i += 1;
      const t = n.target;
      lines.push(
        `### ${i}. <${t.tag}>${t.id ? ` #${t.id}` : ''}${t.cls ? ` .${t.cls.split(' ').join('.')}` : ''}`,
        `- 元素文本：${t.text || '（无）'}`,
        `- 选择器：\`${t.selector}\``,
        `- 记录时间：${n.ts}`,
        `- 意见：${n.note.trim() || '（未填写）'}`,
        '',
      );
    }
  }
  return lines.join('\n');
}

const HOVER_STYLE_ID = 'fy-review-hover-style';

export function ReviewMode() {
  const [active, setActive] = useState(false);
  const [notes, setNotes] = useState<ReviewNote[]>(loadNotes);
  const hoveredRef = useRef<HTMLElement | null>(null);

  useEffect(() => {
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(notes));
    } catch {
      // storage full/unavailable: comments stay in memory for this session
    }
  }, [notes]);

  const clearHover = useCallback(() => {
    if (hoveredRef.current) {
      hoveredRef.current.classList.remove('fy-review-hover');
      hoveredRef.current = null;
    }
  }, []);

  useEffect(() => {
    if (!active) {
      clearHover();
      return;
    }
    let style = document.getElementById(HOVER_STYLE_ID);
    if (!style) {
      style = document.createElement('style');
      style.id = HOVER_STYLE_ID;
      style.textContent =
        '.fy-review-hover{outline:2px dashed var(--sky,#0284c7) !important;' +
        'outline-offset:2px;cursor:crosshair !important;}';
      document.head.appendChild(style);
    }

    const inReviewUi = (t: EventTarget | null): boolean =>
      t instanceof Element && t.closest(`[${REVIEW_UI_ATTR}]`) !== null;

    const onOver = (e: MouseEvent): void => {
      if (inReviewUi(e.target)) {
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
      if (inReviewUi(e.target)) return; // 评审面板自身正常交互
      e.preventDefault();
      e.stopPropagation();
      const el = e.target instanceof HTMLElement ? e.target : null;
      if (!el) return;
      const note: ReviewNote = {
        id: `n-${Date.now()}-${Math.random().toString(36).slice(2, 7)}`,
        ts: new Date().toLocaleTimeString('zh-CN'),
        page: window.location.pathname,
        target: describeTarget(el),
        note: '',
      };
      setNotes((prev) => [...prev, note]);
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
  }, [active, clearHover]);

  const updateNote = (id: string, text: string): void =>
    setNotes((prev) => prev.map((n) => (n.id === id ? { ...n, note: text } : n)));

  const removeNote = (id: string): void =>
    setNotes((prev) => prev.filter((n) => n.id !== id));

  const exportNotes = (): void => {
    const md = buildMarkdown(notes);
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
      {!active && (
        <button
          type="button"
          {...{ [REVIEW_UI_ATTR]: '' }}
          onClick={() => setActive(true)}
          style={{
            position: 'fixed', right: 18, bottom: 18, zIndex: 2147483000,
            padding: '10px 14px', borderRadius: '999px', border: '1px solid var(--glass-border)',
            background: 'var(--glass-strong)', color: 'var(--sky-deep)',
            boxShadow: 'var(--shadow)', cursor: 'pointer', fontSize: 13, fontWeight: 600,
          }}
          title="开启 UI 评审模式：点击任意组件即可在右侧添加修改意见"
        >
          🔍 评审
        </button>
      )}

      {active && (
        <aside
          {...{ [REVIEW_UI_ATTR]: '' }}
          style={{
            position: 'fixed', top: 0, right: 0, height: '100vh', width: 350, zIndex: 2147483000,
            background: 'var(--glass-elevated)', borderLeft: '1px solid var(--line)',
            boxShadow: 'var(--shadow)', display: 'flex', flexDirection: 'column',
            fontFamily: 'inherit',
          }}
        >
          <div style={{ padding: '14px 16px', borderBottom: '1px solid var(--line)' }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
              <strong style={{ color: 'var(--text-main)' }}>UI 评审模式</strong>
              <span style={{ color: 'var(--text-muted)', fontSize: 12 }}>
                点击页面任意组件添加意见（Esc 退出）
              </span>
            </div>
            <div style={{ display: 'flex', gap: 8, marginTop: 10 }}>
              <button type="button" onClick={exportNotes} disabled={notes.length === 0}
                style={{ flex: 1, padding: '7px 0', borderRadius: 10, border: '1px solid var(--ice-soft)',
                  background: 'var(--sky)', color: '#fff', cursor: notes.length ? 'pointer' : 'not-allowed', fontSize: 13 }}>
                导出意见（{notes.length}）
              </button>
              <button type="button" onClick={() => { if (confirm('清空全部评审意见？')) setNotes([]); }}
                style={{ padding: '7px 10px', borderRadius: 10, border: '1px solid var(--amber-border)',
                  background: 'var(--amber-bg)', color: 'var(--amber)', cursor: 'pointer', fontSize: 13 }}>
                清空
              </button>
              <button type="button" onClick={() => setActive(false)}
                style={{ padding: '7px 10px', borderRadius: 10, border: '1px solid var(--line)',
                  background: 'var(--glass-base)', color: 'var(--text-muted)', cursor: 'pointer', fontSize: 13 }}>
                完成
              </button>
            </div>
          </div>

          <div style={{ flex: 1, overflowY: 'auto', padding: 12, display: 'flex', flexDirection: 'column', gap: 10 }}>
            {notes.length === 0 && (
              <p style={{ color: 'var(--text-faint)', fontSize: 13, textAlign: 'center', marginTop: 30 }}>
                还没有意见。点击页面上的任意组件，这里会出现评论卡片。
              </p>
            )}
            {notes.map((n) => (
              <div key={n.id}
                style={{ border: '1px solid var(--line)', borderRadius: 12, padding: 10, background: 'var(--glass-base)' }}>
                <div style={{ fontSize: 12, color: 'var(--sky-deep)', fontWeight: 600, wordBreak: 'break-all' }}>
                  {n.page} · &lt;{n.target.tag}&gt;
                  {n.target.id && ` #${n.target.id}`}
                  {n.target.cls && ` .${n.target.cls.split(' ').join('.')}`}
                </div>
                {n.target.text && (
                  <div style={{ fontSize: 12, color: 'var(--text-muted)', margin: '4px 0' }}>
                    文本：{n.target.text}
                  </div>
                )}
                <textarea
                  autoFocus
                  value={n.note}
                  onChange={(e) => updateNote(n.id, e.target.value)}
                  placeholder="这里写修改意见，例如：按钮太大、文案换成 XX、这个面板挪到左侧…"
                  style={{ width: '100%', minHeight: 56, marginTop: 6, borderRadius: 8,
                    border: '1px solid var(--line)', padding: 8, fontSize: 13,
                    background: '#fff', color: 'var(--text-main)', resize: 'vertical', boxSizing: 'border-box' }}
                />
                <div style={{ display: 'flex', justifyContent: 'space-between', marginTop: 6 }}>
                  <span style={{ fontSize: 11, color: 'var(--text-faint)' }}>{n.ts} · {n.target.selector}</span>
                  <button type="button" onClick={() => removeNote(n.id)}
                    style={{ fontSize: 12, color: 'var(--rose)', background: 'none',
                      border: 'none', cursor: 'pointer' }}>
                    删除
                  </button>
                </div>
              </div>
            ))}
          </div>
        </aside>
      )}
    </>
  );
}
