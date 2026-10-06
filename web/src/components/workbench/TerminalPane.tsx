import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { workbenchApi, type TerminalSession, type TerminalRead } from '../../api/workbench';
import { errorMessage } from '../ui';
import { LineIcon } from '../ui/LineIcon';
import { WbIcon } from './WbIcon';
import { isComposingLike } from './wbKeys';

interface Props {
  workspaceId: string;
  /**
   * 多标签隔离作用域。缺省时沿用历史键 `fy.terminal.<workspaceId>`，
   * 保证 single-pane 语义与既有刷新重连测试一字不变；附加标签用 `t2`/`t3`…
   * 各记各的服务端会话，互不抢占。
   */
  sessionScope?: string;
  /** 停止是即时动作（无二次确认），但要给父层机会补一条可逆提示。 */
  onSessionStopped?: (info: { sessionId: string; reason: string }) => void;
  /** 会话快照回传（给右区「验证状态卡」提供真实退出码，不许画假数据）。 */
  onSessionChange?: (snap: {
    sessionId: string | null;
    state: string | null;
    exitCode: number | null;
  }) => void;
  /**
   * 「重新启动会话」的受控信号（递增即触发一次 createSession）。
   * 停止是即时动作、不可逆地杀掉 PTY，所以可逆性必须由外部补一条带动作的
   * 提示（08-规范 §6 第 10 条：停止后 toast `[重新启动会话]`）。
   */
  restartSignal?: number;
}

const COLS = 120;
const ROWS = 24;
const MAX_HISTORY = 120;

/**
 * Session ids are persisted per workspace so a page refresh reattaches to the
 * still-running server-side PTY instead of stranding it (18 §C: the process
 * registry lives in the server, not in the page).
 */
function storageKey(workspaceId: string, scope?: string): string {
  return scope ? `fy.terminal.${workspaceId}.${scope}` : `fy.terminal.${workspaceId}`;
}

function readStoredSession(workspaceId: string, scope?: string): string | null {
  try {
    return window.localStorage.getItem(storageKey(workspaceId, scope));
  } catch {
    return null;
  }
}

function writeStoredSession(workspaceId: string, scope: string | undefined, sessionId: string | null): void {
  try {
    const k = storageKey(workspaceId, scope);
    if (sessionId) window.localStorage.setItem(k, sessionId);
    else window.localStorage.removeItem(k);
  } catch {
    /* private mode / quota — reconnect degrades, the app still works */
  }
}

/** 极简模糊匹配： subsequence + 连续命中加权，够用且不引入依赖。 */
function fuzzyScore(hay: string, needle: string): number {
  const h = hay.toLowerCase();
  const n = needle.toLowerCase();
  if (!n) return 0;
  let score = 0;
  let hi = 0;
  let streak = 0;
  for (const ch of n) {
    const found = h.indexOf(ch, hi);
    if (found === -1) return -1;
    streak = found === hi ? streak + 1 : 0;
    score += 1 + streak;
    hi = found + 1;
  }
  return score;
}

interface ContextMenuState {
  x: number;
  y: number;
  hasSelection: boolean;
}

export function TerminalPane({ workspaceId, sessionScope, onSessionStopped, onSessionChange, restartSignal }: Props) {
  const [session, setSession] = useState<TerminalSession | null>(null);
  const [output, setOutput] = useState('');
  const [input, setInput] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [status, setStatus] = useState<string>('');
  const [busy, setBusy] = useState(false);
  const [reattaching, setReattaching] = useState(false);

  // ---- 命令历史 / 补全 / 模糊检索 ----
  const [history, setHistory] = useState<string[]>([]);
  const [histCursor, setHistCursor] = useState<number | null>(null);
  const [searchOpen, setSearchOpen] = useState(false);
  const [searchTerm, setSearchTerm] = useState('');
  const [searchHit, setSearchHit] = useState(0);

  // ---- 行选择与自动滚动 ----
  const [selFrom, setSelFrom] = useState<number | null>(null);
  const [selTo, setSelTo] = useState<number | null>(null);
  const [pinnedToBottom, setPinnedToBottom] = useState(true);
  const [menu, setMenu] = useState<ContextMenuState | null>(null);
  const [runSummary, setRunSummary] = useState<string>('');

  const pollRef = useRef<number | null>(null);
  const scrollRef = useRef<HTMLDivElement | null>(null);
  const inputRef = useRef<HTMLInputElement | null>(null);
  const dragRef = useRef<number | null>(null);
  const stoppedRef = useRef(false);
  // Reattach fires on mount, so it can still be awaiting /terminals while the
  // user clicks「启动终端」. Whoever starts LAST owns the session state: a stale
  // reattach must never null out (or overwrite) a session created after it
  // began, which used to leave the pane on「启动终端」with no input row.
  const reattachGenRef = useRef(0);
  // The server returns the buffer from its own committed cursor (this client
  // never advances it), so each poll is authoritative and REPLACES the view.
  // That is also what makes a reattach replay everything missed while away.
  const READ_WAIT_SECONDS = 0.5;

  const lines = useMemo(() => output.split('\n'), [output]);

  const scrollToBottom = useCallback(() => {
    const el = scrollRef.current;
    if (el) el.scrollTop = el.scrollHeight;
    setPinnedToBottom(true);
  }, []);

  const stopPolling = useCallback(() => {
    if (pollRef.current) {
      window.clearTimeout(pollRef.current);
      pollRef.current = null;
    }
  }, []);

  const onScroll = useCallback(() => {
    const el = scrollRef.current;
    if (!el) return;
    // 用户上滚即视为接管滚动权，后续轮询不再把他拽回底部。
    const atBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 24;
    setPinnedToBottom(atBottom);
  }, []);

  const startPolling = useCallback(
    (sessionId: string) => {
      stopPolling();
      const poll = async () => {
        if (stoppedRef.current) return;
        try {
          // NOTE: the second argument is the server-side wait, NOT a cursor —
          // passing a buffer offset here would exceed the wait cap and 422.
          const r: TerminalRead = await workbenchApi.readTerminal(sessionId, READ_WAIT_SECONDS);
          setOutput(r.output);
          if (r.state !== 'running') {
            setSession((s) => (s ? { ...s, state: r.state, exit_code: r.exit_code } : s));
            setStatus(r.state === 'running' ? '' : `会话已结束：${r.state}`);
            return;
          }
        } catch {
          // A transient read failure must not kill the loop; the session is still
          // alive server-side.
        }
        pollRef.current = window.setTimeout(poll, 250);
      };
      pollRef.current = window.setTimeout(poll, 200);
    },
    [stopPolling],
  );

  const adopt = useCallback(
    (sess: TerminalSession, note: string) => {
      setSession(sess);
      setStatus(note);
      stoppedRef.current = false;
      startPolling(sess.id);
    },
    [startPolling],
  );

  useEffect(() => {
    onSessionChange?.({
      sessionId: session?.id ?? null,
      state: session?.state ?? null,
      exitCode: session?.exit_code ?? null,
    });
  }, [session?.id, session?.state, session?.exit_code, onSessionChange]);

  // 外部「重新启动会话」信号：跳过 0（初始值）只响应递增，避免挂载即启动。
  const lastRestart = useRef(restartSignal ?? 0);
  useEffect(() => {
    if (restartSignal === undefined) return;
    if (restartSignal === lastRestart.current) return;
    lastRestart.current = restartSignal;
    void createSession();
    // createSession 是稳定的一次性动作，无需进依赖表。
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [restartSignal]);

  async function createSession() {
    // Claim the state: cancel any reattach still in flight and stop it from
    // clearing the spinner we are about to own.
    reattachGenRef.current += 1;
    setReattaching(false);
    setBusy(true);
    setError(null);
    stoppedRef.current = false;
    setOutput('');
    setRunSummary('');
    try {
      const sess = await workbenchApi.createTerminal(workspaceId, { cols: COLS, rows: ROWS });
      writeStoredSession(workspaceId, sessionScope, sess.id);
      adopt(sess, '');
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setBusy(false);
    }
  }

  /**
   * Reattach: ask the server which sessions this workspace still has, prefer the
   * one we created before the refresh, and resume it. Nothing is restarted and
   * no server-side session is silently dropped.
   */
  const reattach = useCallback(async () => {
    const gen = ++reattachGenRef.current;
    const stale = () => gen !== reattachGenRef.current;
    setReattaching(true);
    setError(null);
    try {
      const list = await workbenchApi.listTerminals(workspaceId);
      if (stale()) return;
      const running = list.items.filter((s) => s.state === 'running');
      const remembered = readStoredSession(workspaceId, sessionScope);
      const target =
        running.find((s) => s.id === remembered) ?? running[running.length - 1] ?? null;

      if (!target) {
        // Nothing survived. Forget the stale id so we do not keep probing it.
        writeStoredSession(workspaceId, sessionScope, null);
        stoppedRef.current = true;
        stopPolling();
        setSession(null);
        setOutput('');
        setStatus(
          running.length > 0
            ? '本工作区没有运行中的终端会话。'
            : '刷新后未找到可重连的会话（旧会话已结束或被回收）。',
        );
        return;
      }

      const described = await workbenchApi.describeTerminal(target.id);
      if (stale()) return;
      adopt(described, `已重连到既有会话 ${described.id}（刷新前创建，服务端仍在运行）`);
    } catch (e) {
      if (stale()) return;
      // A 404 means the remembered session is gone; fall back to the list.
      setStatus('刷新后未找到可重连的会话，可重新启动终端。');
      writeStoredSession(workspaceId, sessionScope, null);
      try {
        void errorMessage(e);
      } catch { /* never surface a reattach failure as a crash */ }
    } finally {
      if (!stale()) setReattaching(false);
    }
  }, [workspaceId, sessionScope, adopt, stopPolling]);

  useEffect(() => {
    stoppedRef.current = false;
    void reattach();
    return () => {
      stoppedRef.current = true;
      stopPolling();
    };
  }, [workspaceId, reattach, stopPolling]);

  useEffect(() => {
    if (pinnedToBottom && scrollRef.current) scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
  }, [output, pinnedToBottom]);

  const selectedText = useMemo(() => {
    if (selFrom === null) return '';
    const to = selTo ?? selFrom;
    const lo = Math.min(selFrom, to);
    const hi = Math.max(selFrom, to);
    return lines.slice(lo, hi + 1).join('\n');
  }, [lines, selFrom, selTo]);

  const completionCandidates = useMemo(() => {
    // Tab 补全候选来自「本会话可见文本」中最像命令/路径的 token——
    // 不假装能读远端文件系统，补全失败时明说是本地候选。
    const words = new Set<string>();
    for (const raw of history) words.add(raw.trim().split(/\s+/)[0]);
    for (const ln of lines.slice(-120)) {
      for (const m of ln.matchAll(/[A-Za-z0-9_./\\-]{3,}/g)) words.add(m[0]);
    }
    return Array.from(words).filter(Boolean);
  }, [lines, history]);

  const searchResults = useMemo(() => {
    if (!searchTerm) return history.slice(-12).reverse();
    return history
      .map((h, i) => ({ h, i, s: fuzzyScore(h, searchTerm) }))
      .filter((x) => x.s > 0)
      .sort((a, b) => b.s - a.s)
      .slice(0, 12)
      .map((x) => x.h);
  }, [history, searchTerm]);

  async function send() {
    if (!session || !input) return;
    const data = input;
    setInput('');
    setHistCursor(null);
    setSearchOpen(false);
    try {
      // Enter on Windows ConPTY/cmd is CR (\r); an LF-only payload is echoed
      // but never submitted, so the command silently does not run.
      await workbenchApi.writeTerminal(session.id, data + '\r');
      setHistory((h) => [...h.filter((x) => x !== data), data].slice(-MAX_HISTORY));
      setRunSummary(`已发送：${data}`);
    } catch (e) {
      setError(errorMessage(e));
    }
  }

  async function stop() {
    if (!session) return;
    stoppedRef.current = true;
    stopPolling();
    try {
      const r = await workbenchApi.stopTerminal(session.id);
      writeStoredSession(workspaceId, sessionScope, null);
      setSession((s) => (s ? { ...s, state: r.state, stop_reason: r.stop_reason } : s));
      setStatus(`会话已停止：${r.stop_reason ?? r.state}`);
      // 停止是即时动作：ui-team.spec 在单击「停止」后立刻断言输入框消失，
      // 中间不能插确认弹窗。可逆性靠副标题文案 + 父层 toast 的「重新启动会话」。
      onSessionStopped?.({ sessionId: session.id, reason: r.stop_reason ?? r.state });
    } catch (e) {
      setError(errorMessage(e));
    }
  }

  function applyCompletion() {
    const tail = input.split(/\s+/).pop() ?? '';
    if (!tail) return;
    const hit = completionCandidates.find((c) => c.startsWith(tail) && c !== tail);
    if (!hit) {
      setRunSummary(`没有以「${tail}」开头的本地候选（补全只看本会话可见文本）。`);
      return;
    }
    setInput(`${input.slice(0, input.length - tail.length)}${hit}`);
  }

  function onInputKeyDown(e: React.KeyboardEvent<HTMLInputElement>) {
    if (isComposingLike(e)) return;

    if (searchOpen) {
      if (e.key === 'Escape') {
        e.preventDefault();
        setSearchOpen(false);
        return;
      }
      if (e.key === 'ArrowDown') {
        e.preventDefault();
        setSearchHit((h) => Math.min(h + 1, Math.max(0, searchResults.length - 1)));
        return;
      }
      if (e.key === 'ArrowUp') {
        e.preventDefault();
        setSearchHit((h) => Math.max(0, h - 1));
        return;
      }
      if (e.key === 'Enter') {
        e.preventDefault();
        const pick = searchResults[searchHit];
        if (pick !== undefined) setInput(pick);
        setSearchOpen(false);
        return;
      }
      // Ctrl+R 在检索面板内继续输入；其余键照常落到输入框。
      return;
    }

    if (e.key === 'Enter') {
      e.preventDefault();
      void send();
      return;
    }
    if (e.key === 'Tab') {
      e.preventDefault();
      applyCompletion();
      return;
    }
    if (e.key === 'ArrowUp') {
      e.preventDefault();
      if (history.length === 0) return;
      const next = histCursor === null ? history.length - 1 : Math.max(0, histCursor - 1);
      setHistCursor(next);
      setInput(history[next] ?? '');
      return;
    }
    if (e.key === 'ArrowDown') {
      e.preventDefault();
      if (histCursor === null) return;
      const next = histCursor + 1;
      if (next >= history.length) {
        setHistCursor(null);
        setInput('');
      } else {
        setHistCursor(next);
        setInput(history[next] ?? '');
      }
      return;
    }
    // Ctrl+R 是浏览器「刷新」保留键，仅在终端输入聚焦时才接管，
    // 并在标签上给出 Alt+R 兜底与指针入口（历史按钮）。
    if (e.key.toLowerCase() === 'r' && (e.ctrlKey || e.metaKey || e.altKey)) {
      e.preventDefault();
      setSearchHit(0);
      setSearchOpen(true);
    }
  }

  // ---- 行选择：点选整行 / 拖拽多行 ----
  function onLinePointerDown(i: number) {
    dragRef.current = i;
    setSelFrom(i);
    setSelTo(i);
    const el = scrollRef.current;
    if (!el) return;
    const move = (ev: PointerEvent) => {
      const target = document.elementFromPoint(ev.clientX, ev.clientY);
      const row = target?.closest?.('[data-line-index]') as HTMLElement | null;
      if (row?.dataset.lineIndex) setSelTo(Number(row.dataset.lineIndex));
    };
    const up = () => {
      dragRef.current = null;
      el.removeEventListener('pointermove', move);
      window.removeEventListener('pointerup', up);
    };
    el.addEventListener('pointermove', move);
    window.addEventListener('pointerup', up);
  }

  function onContextMenu(e: React.MouseEvent) {
    const target = e.target as HTMLElement;
    const row = target.closest('[data-line-index]') as HTMLElement | null;
    if (row?.dataset.lineIndex) {
      const i = Number(row.dataset.lineIndex);
      if (selFrom === null) {
        setSelFrom(i);
        setSelTo(i);
      }
    }
    setMenu({ x: e.clientX, y: e.clientY, hasSelection: selectedText.length > 0 });
    e.preventDefault();
  }

  useEffect(() => {
    if (!menu) return;
    const close = () => setMenu(null);
    window.addEventListener('pointerdown', close);
    window.addEventListener('scroll', close, true);
    return () => {
      window.removeEventListener('pointerdown', close);
      window.removeEventListener('scroll', close, true);
    };
  }, [menu]);

  async function copySelection() {
    const text = selectedText || window.getSelection()?.toString() || '';
    if (!text) return;
    try {
      await navigator.clipboard.writeText(text);
      setRunSummary(`已复制 ${text.split('\n').length} 行到剪贴板。`);
    } catch {
      setRunSummary('浏览器拒绝了剪贴板写入，请改用系统右键菜单复制。');
    }
    setMenu(null);
  }

  async function pasteToTerminal() {
    try {
      const text = await navigator.clipboard.readText();
      if (!text) return;
      const single = text.replace(/\r?\n/g, ' ').trim();
      setInput((cur) => (cur ? `${cur} ${single}` : single));
      setRunSummary('已把剪贴板内容填入输入框（多行已压成单行，回车才会真正发送）。');
    } catch {
      setRunSummary('浏览器拒绝了剪贴板读取，请改用 Ctrl+V 直接粘贴。');
    }
    setMenu(null);
  }

  function runSelectedLine() {
    const text = selectedText.trim();
    if (!text) return;
    const first = text.split('\n')[0].trim();
    setInput(first);
    setMenu(null);
    window.setTimeout(() => inputRef.current?.focus(), 0);
    setRunSummary(`已把「${first}」填入输入框，回车即运行。`);
  }

  const running = session?.state === 'running';
  const isSel = (i: number) =>
    selFrom !== null && i >= Math.min(selFrom, selTo ?? selFrom) && i <= Math.max(selFrom, selTo ?? selFrom);

  return (
    <div className="terminal-pane" data-state={session?.state ?? 'none'}>
      <div className="terminal-head">
        <div className="terminal-head-left">
          <strong className="terminal-title">交互终端</strong>
          {session && (
            <span className={`ui-badge ${running ? 'ui-badge--running' : 'ui-badge--paused'} wb-term-badge`}>
              {running ? '运行中' : session.state === 'stopped' ? '已停止' : session.state}
            </span>
          )}
          {session && (
            <span className="muted wb-term-meta">
              {session.pty_backend}
              {session.exit_code !== null && session.exit_code !== undefined
                ? ` · 退出码 ${session.exit_code}`
                : ''}
            </span>
          )}
        </div>
        <div className="terminal-head-right">
          {reattaching && <span className="muted wb-term-note">正在查找可重连会话…</span>}
          {!session ? (
            <button
              type="button"
              className="ui-btn ui-btn--sm ui-btn--primary"
              onClick={() => void createSession()}
              disabled={busy || reattaching}
            >
              {busy ? '启动中…' : '启动终端'}
            </button>
          ) : running ? (
            <button
              type="button"
              className="ui-btn ui-btn--sm ui-btn--danger wb-term-stop"
              onClick={() => void stop()}
              title="停止会话（输出保留，可立即重新启动）"
            >
              停止
            </button>
          ) : (
            // A finished session keeps its output readable, but the user must
            // still be able to open a new shell without reloading the page.
            <>
              <span className="muted wb-term-note">输出保留，可立即重新启动</span>
              <button
                type="button"
                className="ui-btn ui-btn--sm ui-btn--primary"
                onClick={() => void createSession()}
                disabled={busy || reattaching}
              >
                {busy ? '启动中…' : '启动终端'}
              </button>
            </>
          )}
        </div>
      </div>

      {error && <div className="error-text wb-term-error" role="alert">{error}</div>}
      {status && !runSummary && <div className="muted wb-term-status" role="status">{status}</div>}

      <div className="terminal-body">
        {/* role="log" + aria-live="off"：终端输出逐行朗读会把屏幕阅读器淹没，
            命令结果改用下方独立的 role=status 摘要播报。 */}
        <div
          className="terminal-output"
          ref={scrollRef}
          onScroll={onScroll}
          onContextMenu={onContextMenu}
          role="log"
          aria-live="off"
          aria-label="终端输出"
        >
          {lines.length === 1 && !lines[0] ? (
            <div className="terminal-placeholder">
              点击「启动终端」以创建真实 PTY 会话（仅在已接入的受信任工作区可用）。
            </div>
          ) : (
            lines.map((ln, i) => (
              <div
                key={i}
                className={`terminal-line${isSel(i) ? ' is-selected' : ''}`}
                data-line-index={i}
                onPointerDown={() => onLinePointerDown(i)}
              >
                {/* 行号对比度：需求给的 rgba(255,255,255,.3) 在深底上不足 3:1，
                    这里提到 .45，其余配色一字未改。 */}
                <span className="terminal-ln" aria-hidden="true">{i + 1}</span>
                <span className="terminal-ln-text">{ln || ' '}</span>
              </div>
            ))
          )}
        </div>

        {!pinnedToBottom && (
          <button type="button" className="wb-term-newcontent" onClick={scrollToBottom}>
            有新内容 ↓
          </button>
        )}
      </div>

      {running && (
        <div className="terminal-input-row">
          <span className="terminal-prompt" aria-hidden="true">›</span>
          <input
            ref={inputRef}
            value={input}
            placeholder="输入命令，回车发送 · ↑ 历史 · Ctrl+R 检索 · Tab 补全"
            aria-label="终端输入"
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={onInputKeyDown}
          />
          <button type="button" className="ui-btn ui-btn--sm ui-btn--icon" onClick={() => void send()} aria-label="发送命令">
            <WbIcon name="send" size={16} />
          </button>
          <button
            type="button"
            className="ui-btn ui-btn--sm ui-btn--icon"
            aria-label="检索命令历史（Ctrl+R / Alt+R）"
            title="检索命令历史（Ctrl+R / Alt+R）"
            onClick={() => {
              setSearchHit(0);
              setSearchOpen(true);
              inputRef.current?.focus();
            }}
          >
            <WbIcon name="history" size={16} />
          </button>
        </div>
      )}

      {searchOpen && (
        <div className="wb-term-search" role="dialog" aria-label="命令历史检索">
          <div className="wb-term-search-hd">
            <LineIcon name="search" size={16} />
            <input
              autoFocus
              value={searchTerm}
              onChange={(e) => {
                setSearchTerm(e.target.value);
                setSearchHit(0);
              }}
              onKeyDown={onInputKeyDown}
              placeholder="模糊匹配历史命令，↑↓ 选择，回车填入，Esc 关闭"
              aria-label="命令历史模糊检索"
            />
            <span className="ui-kbd">Esc</span>
          </div>
          {searchResults.length === 0 ? (
            <div className="muted wb-term-search-empty">
              {searchTerm ? `没有历史命令匹配「${searchTerm}」。` : '本会话还没有命令历史。'}
            </div>
          ) : (
            <ul className="wb-term-search-list">
              {searchResults.map((h, i) => (
                <li key={`${h}-${i}`}>
                  <button
                    type="button"
                    className={i === searchHit ? 'is-active' : undefined}
                    onMouseEnter={() => setSearchHit(i)}
                    onClick={() => {
                      setInput(h);
                      setSearchOpen(false);
                      inputRef.current?.focus();
                    }}
                  >
                    {h}
                  </button>
                </li>
              ))}
            </ul>
          )}
          <p className="wb-term-search-foot muted">
            候选仅来自本标签本次会话发出的命令；服务端 PTY 历史不在浏览器侧留存。
          </p>
        </div>
      )}

      {/* 独立摘要通道：不逐行朗读输出，只在关键结果出现时播报一句。 */}
      <div className="wb-term-summary" role="status" aria-live="polite">
        {runSummary}
      </div>

      {menu && (
        <ul
          className="wb-ctx"
          style={{ left: menu.x, top: menu.y }}
          role="menu"
          aria-label="终端右键菜单"
        >
          <li role="none">
            <button type="button" role="menuitem" onClick={() => void copySelection()}>
              复制选中行{menu.hasSelection ? '' : '（当前无选中行）'}
            </button>
          </li>
          <li role="none">
            <button type="button" role="menuitem" onClick={() => void pasteToTerminal()}>
              粘贴到输入框
            </button>
          </li>
          <li role="none">
            <button type="button" role="menuitem" onClick={runSelectedLine}>
              把选中行填入输入框
            </button>
          </li>
          <li role="none">
            <button type="button" role="menuitem" onClick={() => { setSelFrom(null); setSelTo(null); setMenu(null); }}>
              清除行选择
            </button>
          </li>
        </ul>
      )}
    </div>
  );
}
