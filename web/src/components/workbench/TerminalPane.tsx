import { useCallback, useEffect, useRef, useState } from 'react';
import { workbenchApi, type TerminalSession, type TerminalRead } from '../../api/workbench';
import { errorMessage } from '../ui';

interface Props {
  workspaceId: string;
}

const COLS = 120;
const ROWS = 24;

/**
 * Session ids are persisted per workspace so a page refresh reattaches to the
 * still-running server-side PTY instead of stranding it (18 §C: the process
 * registry lives in the server, not in the page).
 */
function storageKey(workspaceId: string): string {
  return `fy.terminal.${workspaceId}`;
}

function readStoredSession(workspaceId: string): string | null {
  try {
    return window.localStorage.getItem(storageKey(workspaceId));
  } catch {
    return null;
  }
}

function writeStoredSession(workspaceId: string, sessionId: string | null): void {
  try {
    if (sessionId) window.localStorage.setItem(storageKey(workspaceId), sessionId);
    else window.localStorage.removeItem(storageKey(workspaceId));
  } catch {
    /* private mode / quota — reconnect degrades, the app still works */
  }
}

export function TerminalPane({ workspaceId }: Props) {
  const [session, setSession] = useState<TerminalSession | null>(null);
  const [output, setOutput] = useState('');
  const [input, setInput] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [status, setStatus] = useState<string>('');
  const [busy, setBusy] = useState(false);
  const [reattaching, setReattaching] = useState(false);
  const pollRef = useRef<number | null>(null);
  const scrollRef = useRef<HTMLPreElement | null>(null);
  const stoppedRef = useRef(false);
  // The server returns the buffer from its own committed cursor (this client
  // never advances it), so each poll is authoritative and REPLACES the view.
  // That is also what makes a reattach replay everything missed while away.
  const READ_WAIT_SECONDS = 0.5;

  const stopPolling = useCallback(() => {
    if (pollRef.current) {
      window.clearTimeout(pollRef.current);
      pollRef.current = null;
    }
  }, []);

  const startPolling = useCallback((sessionId: string) => {
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
  }, [stopPolling]);

  const adopt = useCallback((sess: TerminalSession, note: string) => {
    setSession(sess);
    setStatus(note);
    stoppedRef.current = false;
    startPolling(sess.id);
  }, [startPolling]);

  async function createSession() {
    setBusy(true);
    setError(null);
    stoppedRef.current = false;
    setOutput('');
    try {
      const sess = await workbenchApi.createTerminal(workspaceId, { cols: COLS, rows: ROWS });
      writeStoredSession(workspaceId, sess.id);
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
    setReattaching(true);
    setError(null);
    try {
      const list = await workbenchApi.listTerminals(workspaceId);
      const running = list.items.filter((s) => s.state === 'running');
      const remembered = readStoredSession(workspaceId);
      const target =
        running.find((s) => s.id === remembered) ?? running[running.length - 1] ?? null;

      if (!target) {
        // Nothing survived. Forget the stale id so we do not keep probing it.
        writeStoredSession(workspaceId, null);
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
      adopt(described, `已重连到既有会话 ${described.id}（刷新前创建，服务端仍在运行）`);
    } catch (e) {
      // A 404 means the remembered session is gone; fall back to the list.
      setStatus('刷新后未找到可重连的会话，可重新启动终端。');
      writeStoredSession(workspaceId, null);
      try {
        void errorMessage(e);
      } catch { /* never surface a reattach failure as a crash */ }
    } finally {
      setReattaching(false);
    }
  }, [workspaceId, adopt, stopPolling]);

  useEffect(() => {
    stoppedRef.current = false;
    void reattach();
    return () => {
      stoppedRef.current = true;
      stopPolling();
    };
  }, [workspaceId, reattach, stopPolling]);

  useEffect(() => {
    if (scrollRef.current) scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
  }, [output]);

  async function send() {
    if (!session || !input) return;
    const data = input;
    setInput('');
    try {
      // Enter on Windows ConPTY/cmd is CR (\r); an LF-only payload is echoed
      // but never submitted, so the command silently does not run.
      await workbenchApi.writeTerminal(session.id, data + '\r');
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
      writeStoredSession(workspaceId, null);
      setSession((s) => (s ? { ...s, state: r.state, stop_reason: r.stop_reason } : s));
      setStatus(`会话已停止：${r.stop_reason ?? r.state}`);
    } catch (e) {
      setError(errorMessage(e));
    }
  }

  const running = session?.state === 'running';
  return (
    <div className="card terminal-pane">
      <div className="row spread">
        <strong>交互终端</strong>
        {!session ? (
          <div className="row">
            {reattaching && <span className="muted small">正在查找可重连会话…</span>}
            <button className="small" onClick={() => void createSession()} disabled={busy || reattaching}>
              {busy ? '启动中…' : '启动终端'}
            </button>
          </div>
        ) : (
          <span className="muted small">
            {session.pty_backend} · {session.state}
            {running ? (
              <button className="danger small" style={{ marginLeft: '0.5rem' }} onClick={() => void stop()}>
                停止
              </button>
            ) : (
              // A finished session keeps its output readable, but the user must
              // still be able to open a new shell without reloading the page.
              <button className="small" style={{ marginLeft: '0.5rem' }} onClick={() => void createSession()} disabled={busy || reattaching}>
                {busy ? '启动中…' : '启动终端'}
              </button>
            )}
          </span>
        )}
      </div>
      {error && <div className="error-text" role="alert">{error}</div>}
      {status && <div className="muted small" role="status">{status}</div>}
      <pre className="terminal-output" ref={scrollRef}>
        {output || (session ? '' : '点击「启动终端」以创建真实 PTY 会话（仅在已接入的受信任工作区可用）。')}
      </pre>
      {running && (
        <div className="terminal-input-row">
          <input
            value={input}
            placeholder="输入命令，回车发送…"
            aria-label="终端输入"
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter') void send();
            }}
          />
          <button className="small" onClick={() => void send()}>发送</button>
        </div>
      )}
    </div>
  );
}