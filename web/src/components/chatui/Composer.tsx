/**
 * 包 C · 输入区 + 联想层（chatui 私有）
 * ---------------------------------------------------------------------------
 * 键位契约：
 *   - Enter 发送；Shift / Ctrl / ⌘ + Enter 换行
 *   - 发送前必须判 IME 组合期（e.nativeEvent.isComposing 或 keyCode===229），
 *     组合期一律不发送（中文输入法防误发）
 *   - 「/」与「@」仅在行首或前一字符为空白/换行时触发联想
 *   - 联想层内：↑↓ 选择、Enter 插入、Tab 只关闭不插入
 *   - Esc 交给页面统一处理（联想 > 右键菜单 > 抽屉 > 模态 > 中止提交 > 退出输入态）
 *
 * 诚实实现：
 *   - 「/」只列 5 条本地可执行命令；技能不进列表，改为「查看技能目录 →」跳 /skills
 *   - 「@」只接 knowledgeApi.listDocuments()：ready 可选、indexing 禁用并说明原因、
 *     failed 禁用并原样展示 error 原文；无文档/离线都有明确文案，绝不退化成空列表
 */
import { useCallback, useEffect, useImperativeHandle, useMemo, useRef, useState, type Ref } from 'react';
import { knowledgeApi, type KBDocument } from '../../api/knowledge';
import { LineIcon } from '../ui/LineIcon';
import { ChatIcon } from './ChatIcons';

const MAX_REFS = 50;

export type CommandId = 'listen' | 'explore' | 'new' | 'export' | 'clearrefs' | 'skills';

interface CommandDef {
  id: CommandId;
  label: string;
  hint: string;
}

/** 「/」只列本地可执行命令（5 条）+ 一条跳技能目录的入口。 */
const COMMANDS: CommandDef[] = [
  { id: 'listen', label: '/倾听', hint: '切换到倾听模式（默认，不自动派发任务）' },
  { id: 'explore', label: '/探索', hint: '切换到探索模式（可委派专家）' },
  { id: 'new', label: '/新建会话', hint: '开始一个新会话' },
  { id: 'export', label: '/导出当前会话', hint: '把当前会话导出为文本文件' },
  { id: 'clearrefs', label: '/清空引用', hint: '移除已选中的知识库引用' },
  { id: 'skills', label: '查看技能目录 →', hint: '跳到 /skills 查看已安装技能' },
];

type Suggest =
  | { kind: 'cmd'; start: number; query: string }
  | { kind: 'ref'; start: number; query: string }
  | null;

export interface ComposerHandle {
  focus: () => void;
  openRefSuggest: () => void;
  blurComposer: () => void;
  /** 由页面 Esc 优先级链调用（联想层排第一） */
  closeSuggest: () => void;
  /** 当前 textarea，供页面判断焦点是否在输入区 */
  element: () => HTMLTextAreaElement | null;
}

export interface ComposerProps {
  ref?: Ref<ComposerHandle>;
  value: string;
  onChange: (v: string) => void;
  onSubmit: () => void;
  submitting: boolean;
  online: boolean;
  placeholder?: string;
  onCommand: (id: CommandId) => void;
  onPickRef: (doc: KBDocument) => void;
  onSuggestOpenChange?: (open: boolean) => void;
}

/** IME 组合期判定：isComposing 或 keyCode 229（部分输入法只给 229）。 */
function isComposingEvent(e: React.KeyboardEvent<HTMLTextAreaElement>): boolean {
  const ne = e.nativeEvent as unknown as { isComposing?: boolean; keyCode?: number };
  return Boolean(ne.isComposing) || ne.keyCode === 229;
}

/** 只在行首或前一字符为空白/换行时认定触发符。 */
function detectTrigger(text: string, caret: number): Suggest {
  let i = caret - 1;
  let start = -1;
  while (i >= 0) {
    const ch = text[i];
    if (ch === '/' || ch === '@') {
      start = i;
      break;
    }
    if (ch === '\n' || ch === ' ' || ch === '\t') return null;
    i--;
  }
  if (start < 0) return null;
  if (start !== 0) {
    const prev = text[start - 1];
    if (!(prev === '\n' || prev === ' ' || prev === '\t')) return null;
  }
  const query = text.slice(start + 1, caret);
  if (/[\s\n\t]/.test(query)) return null;
  return { kind: text[start] === '/' ? 'cmd' : 'ref', start, query } as Suggest;
}

export function Composer({
  ref,
  value,
  onChange,
  onSubmit,
  submitting,
  online,
  placeholder = '说点什么… 输入 / 用命令，输入 @ 引用知识库文档',
  onCommand,
  onPickRef,
  onSuggestOpenChange,
}: ComposerProps) {
  const taRef = useRef<HTMLTextAreaElement>(null);
  const [suggest, setSuggest] = useState<Suggest>(null);
  const [activeIndex, setActiveIndex] = useState(0);
  const [docs, setDocs] = useState<KBDocument[]>([]);
  const [docsState, setDocsState] = useState<'idle' | 'loading' | 'ready' | 'empty' | 'error' | 'offline'>('idle');
  const [docsMessage, setDocsMessage] = useState<string>('');

  const loadDocs = useCallback(async () => {
    if (typeof navigator !== 'undefined' && navigator.onLine === false) {
      setDocsState('offline');
      setDocsMessage('离线：无法加载知识库文档');
      return;
    }
    setDocsState('loading');
    setDocsMessage('');
    try {
      const res = await knowledgeApi.listDocuments();
      const list = Array.isArray(res?.documents) ? res.documents : [];
      setDocs(list.slice(0, MAX_REFS));
      setDocsState(list.length > 0 ? 'ready' : 'empty');
      setDocsMessage('');
    } catch (e) {
      const msg = (e as { message?: string })?.message ?? '';
      setDocs([]);
      if (typeof navigator !== 'undefined' && navigator.onLine === false) {
        setDocsState('offline');
        setDocsMessage('离线：无法加载知识库文档');
      } else {
        setDocsState('error');
        setDocsMessage(msg || '知识库文档加载失败');
      }
    }
  }, []);

  const open = suggest !== null;

  useEffect(() => {
    onSuggestOpenChange?.(open);
  }, [open, onSuggestOpenChange]);

  useEffect(() => {
    // 打开「@」联想时才拉文档（只读一次，失败可重试）
    if (suggest?.kind === 'ref' && docsState === 'idle') void loadDocs();
  }, [suggest, docsState, loadDocs]);

  const refItems = useMemo(() => {
    if (suggest?.kind !== 'ref') return [];
    const q = suggest.query.trim().toLowerCase();
    const src = q ? docs.filter((d) => d.name.toLowerCase().includes(q)) : docs;
    return src.slice(0, MAX_REFS);
  }, [suggest, docs]);

  const cmdItems = useMemo(() => {
    if (suggest?.kind !== 'cmd') return [];
    const q = suggest.query.trim().toLowerCase();
    if (!q) return COMMANDS;
    return COMMANDS.filter((c) => {
      const key = c.label.replace(/^\//, '').toLowerCase();
      return key.includes(q) || c.label.toLowerCase().includes(q);
    });
  }, [suggest]);

  const itemCount = suggest?.kind === 'ref' ? refItems.length : cmdItems.length;

  function closeSuggest() {
    setSuggest(null);
    setActiveIndex(0);
  }

  function syncFromEvent(el: HTMLTextAreaElement) {
    const caret = el.selectionStart ?? el.value.length;
    const next = detectTrigger(el.value, caret);
    setSuggest(next);
    setActiveIndex(0);
  }

  function applyRef(doc: KBDocument) {
    if (!suggest || suggest.kind !== 'ref') return;
    const el = taRef.current;
    const caret = el?.selectionStart ?? value.length;
    const insert = `@《${doc.name}》`;
    const head = value.slice(0, suggest.start);
    const tail = value.slice(caret);
    const nextValue = `${head}${insert} ${tail}`;
    onChange(nextValue);
    onPickRef(doc);
    closeSuggest();
    requestAnimationFrame(() => {
      const pos = head.length + insert.length + 1;
      el?.focus();
      el?.setSelectionRange(pos, pos);
    });
  }

  function applyCommand(id: CommandId) {
    if (!suggest || suggest.kind !== 'cmd') return;
    const el = taRef.current;
    const caret = el?.selectionStart ?? value.length;
    // 命令是本地动作，输入区里只删掉这段触发文本，不随消息发送
    const nextValue = `${value.slice(0, suggest.start)}${value.slice(caret)}`;
    onChange(nextValue);
    closeSuggest();
    onCommand(id);
    requestAnimationFrame(() => {
      const pos = suggest.start;
      el?.focus();
      el?.setSelectionRange(pos, pos);
    });
  }

  function insertActive(): boolean {
    if (!suggest) return false;
    if (suggest.kind === 'ref') {
      const doc = refItems[activeIndex];
      if (!doc) return false;
      if (doc.status !== 'ready') return false; // indexing / failed 不可引用
      applyRef(doc);
      return true;
    }
    const cmd = cmdItems[activeIndex];
    if (!cmd) return false;
    applyCommand(cmd.id);
    return true;
  }

  useImperativeHandle(
    ref,
    () => ({
      focus: () => taRef.current?.focus(),
      openRefSuggest: () => {
        const el = taRef.current;
        if (!el) return;
        el.focus();
        const cur = el.value;
        const caret = cur.length;
        const prev = caret > 0 ? cur[caret - 1] : '';
        const sep = caret === 0 || prev === '\n' || prev === ' ' || prev === '\t' ? '' : ' ';
        const nextValue = `${cur}${sep}@`;
        onChange(nextValue);
        requestAnimationFrame(() => {
          const pos = nextValue.length;
          el.focus();
          el.setSelectionRange(pos, pos);
          setSuggest({ kind: 'ref', start: pos - 1, query: '' });
          setActiveIndex(0);
          void loadDocs();
        });
      },
      blurComposer: () => taRef.current?.blur(),
      closeSuggest: () => closeSuggest(),
      element: () => taRef.current,
    }),
    [onChange, loadDocs],
  );

  function onKeyDown(e: React.KeyboardEvent<HTMLTextAreaElement>) {
    if (suggest) {
      if (e.key === 'ArrowDown') {
        e.preventDefault();
        if (itemCount > 0) setActiveIndex((i) => (i + 1) % itemCount);
        return;
      }
      if (e.key === 'ArrowUp') {
        e.preventDefault();
        if (itemCount > 0) setActiveIndex((i) => (i - 1 + itemCount) % itemCount);
        return;
      }
      if (e.key === 'Tab') {
        // Tab 只关闭，不插入
        e.preventDefault();
        closeSuggest();
        return;
      }
      if (e.key === 'Enter' && !isComposingEvent(e)) {
        e.preventDefault();
        insertActive();
        return;
      }
    }
    if (e.key === 'Enter') {
      // 组合期不发送（中文 IME 防误发）
      if (isComposingEvent(e)) return;
      // Shift / Ctrl / ⌘ + Enter = 换行
      if (e.shiftKey || e.ctrlKey || e.metaKey) return;
      e.preventDefault();
      if (!submitting) onSubmit();
    }
  }

  const suggestId = 'chatui-suggest';

  return (
    <div className="chatui-composer-wrap">
      {open ? (
        <div className={`chatui-suggest chatui-suggest--${suggest?.kind ?? 'cmd'}`}>
          <ul
            className="ui-scroll chatui-suggest-list"
            id={suggestId}
            role="listbox"
            aria-label={suggest?.kind === 'ref' ? '知识库文档引用' : '本地命令'}
          >
            {suggest?.kind === 'ref' && docsState !== 'ready' ? (
              <li role="presentation" className="chatui-suggest-note">
                {docsState === 'loading' ? (
                  <span className="chatui-suggest-note-row">正在加载知识库文档…</span>
                ) : null}
                {docsState === 'empty' ? (
                  <span className="chatui-suggest-note-row">
                    知识库还没有可引用文档。
                    <a className="chatui-suggest-link" href="/knowledge">去知识库上传 →</a>
                  </span>
                ) : null}
                {docsState === 'offline' || docsState === 'error' ? (
                  <span className="chatui-suggest-note-row">
                    {docsMessage}
                    <button type="button" className="ui-btn ui-btn--sm" onClick={() => void loadDocs()}>
                      重试
                    </button>
                  </span>
                ) : null}
              </li>
            ) : null}

            {suggest?.kind === 'ref' && docsState === 'ready'
              ? refItems.map((d, i) => {
                  const disabled = d.status !== 'ready';
                  return (
                    <li key={d.id}>
                      <button
                        type="button"
                        role="option"
                        aria-selected={i === activeIndex}
                        aria-disabled={disabled}
                        disabled={disabled}
                        id={`${suggestId}-opt-${i}`}
                        className={`chatui-suggest-item${i === activeIndex ? ' is-active' : ''}`}
                        onMouseEnter={() => setActiveIndex(i)}
                        onClick={() => applyRef(d)}
                      >
                        <span className="chatui-suggest-main">
                          <span className="chatui-suggest-title">《{d.name}》</span>
                          <span className="chatui-suggest-meta">
                            {d.chunk_count} 段 · {d.source}
                          </span>
                        </span>
                        {d.status === 'indexing' ? (
                          <span className="chatui-suggest-flag">索引中，暂不可引用</span>
                        ) : null}
                        {d.status === 'failed' ? (
                          <span className="chatui-suggest-flag chatui-suggest-flag--failed">
                            {d.error || '索引失败'}
                          </span>
                        ) : null}
                      </button>
                    </li>
                  );
                })
              : null}

            {suggest?.kind === 'ref' && docsState === 'ready' && refItems.length === 0 ? (
              <li role="presentation" className="chatui-suggest-note">
                <span className="chatui-suggest-note-row">
                  没有匹配的知识库文档。
                  <a className="chatui-suggest-link" href="/knowledge">去知识库查看 →</a>
                </span>
              </li>
            ) : null}

            {suggest?.kind === 'cmd'
              ? cmdItems.map((c, i) => (
                  <li key={c.id}>
                    <button
                      type="button"
                      role="option"
                      aria-selected={i === activeIndex}
                      id={`${suggestId}-opt-${i}`}
                      className={`chatui-suggest-item${i === activeIndex ? ' is-active' : ''}`}
                      onMouseEnter={() => setActiveIndex(i)}
                      onClick={() => applyCommand(c.id)}
                    >
                      <span className="chatui-suggest-main">
                        <span className="chatui-suggest-title">{c.label}</span>
                        <span className="chatui-suggest-meta">{c.hint}</span>
                      </span>
                    </button>
                  </li>
                ))
              : null}
          </ul>
          <div className="chatui-suggest-foot" aria-hidden="true">
            <span className="ui-kbd">↑</span>
            <span className="ui-kbd">↓</span> 选择 ·
            <span className="ui-kbd">Enter</span> 插入 ·
            <span className="ui-kbd">Tab</span> 关闭
          </div>
        </div>
      ) : null}

      <div className="chatui-composer">
        <textarea
          ref={taRef}
          className="ui-textarea chatui-textarea"
          aria-label="消息内容"
          aria-autocomplete="list"
          aria-expanded={open}
          aria-controls={suggestId}
          aria-activedescendant={open && itemCount > 0 ? `${suggestId}-opt-${activeIndex}` : undefined}
          value={value}
          placeholder={online ? placeholder : '离线：无法发送（消息不会送达）'}
          onChange={(e) => {
            onChange(e.target.value);
            syncFromEvent(e.target);
          }}
          onKeyUp={(e) => {
            if (!suggest) syncFromEvent(e.currentTarget);
          }}
          onClick={(e) => syncFromEvent(e.currentTarget)}
          onKeyDown={onKeyDown}
        />
        <div className="chatui-composer-side">
          <button
            type="button"
            className="ui-btn ui-btn--ghost ui-btn--sm chatui-composer-hint"
            onClick={() => {
              const el = taRef.current;
              if (!el) return;
              el.focus();
              const cur = el.value;
              const sep = cur.length === 0 || /[\s\n\t]$/.test(cur) ? '' : ' ';
              const nextValue = `${cur}${sep}/`;
              onChange(nextValue);
              requestAnimationFrame(() => {
                const pos = nextValue.length;
                el.focus();
                el.setSelectionRange(pos, pos);
                setSuggest({ kind: 'cmd', start: pos - 1, query: '' });
                setActiveIndex(0);
              });
            }}
          >
            <LineIcon name="terminal" size={16} /> 命令
          </button>
          <button
            type="button"
            className="ui-btn ui-btn--ghost ui-btn--sm chatui-composer-hint"
            onClick={() => {
              const el = taRef.current;
              if (!el) return;
              el.focus();
              const cur = el.value;
              const prev = cur.length > 0 ? cur[cur.length - 1] : '';
              const sep = cur.length === 0 || prev === '\n' || prev === ' ' || prev === '\t' ? '' : ' ';
              const nextValue = `${cur}${sep}@`;
              onChange(nextValue);
              requestAnimationFrame(() => {
                const pos = nextValue.length;
                el.focus();
                el.setSelectionRange(pos, pos);
                setSuggest({ kind: 'ref', start: pos - 1, query: '' });
                setActiveIndex(0);
                void loadDocs();
              });
            }}
          >
            <ChatIcon name="quote" size={16} /> 引用
          </button>
        </div>
      </div>
    </div>
  );
}
