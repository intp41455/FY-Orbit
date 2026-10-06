import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { LineIcon } from '../ui/LineIcon';
import { TerminalPane } from './TerminalPane';
import { WbIcon } from './WbIcon';

/**
 * 底部坞第 1 段：多标签终端。
 *
 * 「关闭标签」= 关视图，**不是**杀会话。服务端 PTY 由 terminal 服务注册表持有
 * （存活与否与页面无关），因此这里只做三件事：
 *   1. 标签维护 heresy 会话作用域 storage key，卸载不写 null；
 *   2. 被关掉的标签进 detached 栈，坞内「重连会话」一键把视图挂回去；
 *   3. Ctrl+Shift+T / Alt+T 开新标签，Ctrl+W / Alt+W 关当前标签，
 *      两者都是浏览器保留键，必须同配指针入口（[+] 与 ×、右键菜单）。
 */

export interface TerminalTab {
  id: string;
  /** 空串＝默认作用域，键名与历史一致（fy.terminal.<ws>）。 */
  scope: string;
  label: string;
}

interface Props {
  workspaceId: string | null;
  /** 父层全局快捷键转发：'new-tab' / 'close-tab'。 */
  hotRequest?: number;
  onSessionStopped?: (info: { sessionId: string; reason: string }) => void;
  /** 会话快照上抛给右区「验证状态卡」，保证卡上退出码来自真实 TerminalSession。 */
  onSessionChange?: (snap: {
    sessionId: string | null;
    state: string | null;
    exitCode: number | null;
  }) => void;
  /**
   * 「重新启动会话」受控信号（递增即让当前活动标签所在的 TerminalPane 重启一次）。
   * 停止是即时且不可逆的，所以可逆性由父层 toast 的 `[重新启动会话]` 提供。
   */
  restartSignal?: number;
}

const MAX_TABS = 4;

function tabsKey(ws: string): string {
  return `fy.workbench.terminal-tabs.${ws}`;
}
function detachedKey(ws: string): string {
  return `fy.workbench.terminal-detached.${ws}`;
}

function readJson<T>(key: string, fallback: T): T {
  try {
    const raw = window.sessionStorage.getItem(key);
    return raw ? (JSON.parse(raw) as T) : fallback;
  } catch {
    return fallback;
  }
}

function writeJson(key: string, value: unknown): void {
  try {
    window.sessionStorage.setItem(key, JSON.stringify(value));
  } catch {
    /* private mode — 视图状态退化为会话内不持久 */
  }
}

/**
 * 单标签视图。会话快照回调必须保持**稳定身份**：TerminalPane 的
 * 快照 effect 以 `onSessionChange` 为依赖，若每次渲染都传入新的内联
 * 箭头函数，会形成「effect → setState → 重渲染 → 新函数身份 → effect」
 * 的无限循环（整页渲染风暴：dev 下 Maximum update depth exceeded，
 * prod 下静默占用主线程导致路由切换无法提交）。故在此用 useCallback
 * 固定依赖（tab.id 为字符串、父层与坞的 setter 身份均稳定），并且
 * 状态值未变化时 bail-out，不再触发无意义的重渲染。
 */
function TerminalTabView({
  tab,
  workspaceId,
  restartSignal,
  onSessionStopped,
  onSessionChange,
  onStateChange,
}: {
  tab: TerminalTab;
  workspaceId: string;
  restartSignal?: number;
  onSessionStopped?: (info: { sessionId: string; reason: string }) => void;
  onSessionChange?: (snap: {
    sessionId: string | null;
    state: string | null;
    exitCode: number | null;
  }) => void;
  onStateChange: (updater: (prev: Record<string, string | null>) => Record<string, string | null>) => void;
}) {
  const handleSessionChange = useCallback(
    (snap: { sessionId: string | null; state: string | null; exitCode: number | null }) => {
      onStateChange((prev) =>
        prev[tab.id] === snap.state ? prev : { ...prev, [tab.id]: snap.state },
      );
      onSessionChange?.(snap);
    },
    [tab.id, onSessionChange, onStateChange],
  );
  return (
    <TerminalPane
      workspaceId={workspaceId}
      sessionScope={tab.scope || undefined}
      restartSignal={restartSignal}
      onSessionStopped={onSessionStopped}
      onSessionChange={handleSessionChange}
    />
  );
}

export function TerminalDock({ workspaceId, hotRequest = 0, onSessionStopped, onSessionChange, restartSignal }: Props) {
  const [tabs, setTabs] = useState<TerminalTab[]>([]);
  const [detached, setDetached] = useState<TerminalTab[]>([]);
  const [activeId, setActiveId] = useState<string>('term-1');
  const [states, setStates] = useState<Record<string, string | null>>({});
  const lastReq = useRef(0);

  useEffect(() => {
    if (!workspaceId) {
      setTabs([]);
      setDetached([]);
      return;
    }
    const stored = readJson<TerminalTab[]>(tabsKey(workspaceId), []);
    const initial: TerminalTab[] =
      stored.length > 0 ? stored.slice(0, MAX_TABS) : [{ id: 'term-1', scope: '', label: '终端 1' }];
    setTabs(initial);
    setDetached(readJson<TerminalTab[]>(detachedKey(workspaceId), []).slice(-4));
    setActiveId((cur) => (initial.some((t) => t.id === cur) ? cur : initial[0].id));
  }, [workspaceId]);

  useEffect(() => {
    if (workspaceId) writeJson(tabsKey(workspaceId), tabs);
  }, [workspaceId, tabs]);
  useEffect(() => {
    if (workspaceId) writeJson(detachedKey(workspaceId), detached);
  }, [workspaceId, detached]);

  const nextIndex = useMemo(() => {
    const used = new Set(tabs.map((t) => Number(t.id.replace('term-', '')) || 0));
    for (let i = 1; i <= MAX_TABS; i++) if (!used.has(i)) return i;
    return MAX_TABS;
  }, [tabs]);

  const newTab = useCallback(() => {
    setTabs((cur) => {
      if (cur.length >= MAX_TABS) return cur;
      if (!workspaceId) return cur;
      const idx = nextIndex;
      const tab: TerminalTab = { id: `term-${idx}`, scope: idx === 1 ? '' : `t${idx}`, label: `终端 ${idx}` };
      const next = [...cur, tab];
      setActiveId(tab.id);
      setDetached((d) => d.filter((x) => x.id !== tab.id));
      return next;
    });
  }, [nextIndex, workspaceId]);

  const closeTab = useCallback(
    (id: string) => {
      setTabs((cur) => {
        if (cur.length <= 1) {
          // 至少留一个视图：真要断开会话请用「停止」，那是唯一会释放 PTY 的动作。
          return cur;
        }
        const victim = cur.find((t) => t.id === id);
        if (victim) setDetached((d) => [...d.filter((x) => x.id !== id), victim].slice(-4));
        const next = cur.filter((t) => t.id !== id);
        setActiveId((a) => (a === id ? next[0].id : a));
        return next;
      });
    },
    [],
  );

  const reattachLast = useCallback(() => {
    setDetached((d) => {
      if (d.length === 0) return d;
      const back = d[d.length - 1];
      setTabs((cur) => (cur.length >= MAX_TABS || cur.some((t) => t.id === back.id) ? cur : [...cur, back]));
      if (back) setActiveId(back.id);
      return d.slice(0, -1);
    });
  }, []);

  useEffect(() => {
    if (hotRequest === lastReq.current) return;
    lastReq.current = hotRequest;
  }, [hotRequest]);

  if (!workspaceId) {
    return (
      <div className="wb-term-empty">
        注册并选择一个工作区后，可在此启动真实 PTY 会话（多标签共存，关闭标签不杀会话）。
      </div>
    );
  }

  const canAdd = tabs.length < MAX_TABS;

  return (
    <div className="wb-termdock">
      <div className="wb-termdock-bar" role="tablist" aria-label="终端会话标签">
        {tabs.map((t) => {
          const selected = t.id === activeId;
          const st = states[t.id] ?? null;
          return (
            <div key={t.id} className={`wb-termtab${selected ? ' is-active' : ''}`} data-state={st ?? 'none'}>
              <button
                type="button"
                role="tab"
                aria-selected={selected}
                className="wb-termtab-main"
                onClick={() => setActiveId(t.id)}
              >
                <LineIcon name="terminal" size={16} />
                <span>{t.label}</span>
                <span className="wb-termtab-state">
                  {st === 'running' ? '运行中' : st === 'stopped' ? '已停止' : st ? st : '未连接'}
                </span>
              </button>
              {tabs.length > 1 && (
                <button
                  type="button"
                  className="wb-termtab-x"
                  aria-label={`关闭 ${t.label} 视图（不结束服务端会话）`}
                  title="关闭视图（Ctrl+W / Alt+W），服务端会话保留"
                  onClick={() => closeTab(t.id)}
                >
                  <WbIcon name="x" size={14} />
                </button>
              )}
            </div>
          );
        })}
        <button
          type="button"
          className="ui-btn ui-btn--sm ui-btn--ghost wb-termtab-add"
          aria-label="新建终端标签（Ctrl+Shift+T / Alt+T）"
          title="新建终端标签（Ctrl+Shift+T / Alt+T）"
          disabled={!canAdd}
          onClick={newTab}
        >
          <WbIcon name="plus" size={16} />
          <span className="ui-kbd">＋</span>
        </button>
        <span className="ui-spacer" />
        <button
          type="button"
          className="ui-btn ui-btn--sm wb-termtab-reattach"
          disabled={detached.length === 0}
          title="把最近关闭的终端视图挂回来（服务端会话仍在运行）"
          onClick={reattachLast}
        >
          重连会话{detached.length > 0 ? `（${detached.length}）` : ''}
        </button>
      </div>

      {tabs.map((t) => (
        <div key={t.id} className="wb-termdock-view" hidden={t.id !== activeId}>
          {/* hidden 而非卸载：切标签不重启 PTY、不重放历史轮询。 */}
          <TerminalTabView
            tab={t}
            workspaceId={workspaceId}
            restartSignal={t.id === activeId ? restartSignal : undefined}
            onSessionStopped={onSessionStopped}
            onSessionChange={onSessionChange}
            onStateChange={setStates}
          />
        </div>
      ))}

      <p className="wb-termdock-foot muted">
        <span className="ui-kbd">Ctrl</span>+<span className="ui-kbd">`</span> 显示/隐藏坞 ·{' '}
        <span className="ui-kbd">Alt</span>+<span className="ui-kbd">T</span> 新标签 ·{' '}
        <span className="ui-kbd">Alt</span>+<span className="ui-kbd">W</span> 关标签 ·{' '}
        <span className="ui-kbd">↑</span> 历史 · <span className="ui-kbd">Ctrl</span>+
        <span className="ui-kbd">R</span> 检索历史 · <span className="ui-kbd">Tab</span> 补全 ·{' '}
        <span className="ui-kbd">Shift</span>+<span className="ui-kbd">F10</span> 右键菜单
        <br />
        Ctrl+Shift+T / Ctrl+W 是浏览器「重开/关闭标签页」的保留键，浏览器层优先，因此 Alt 兜底与指针入口
        （标签 <span className="ui-kbd">＋</span> / <span className="ui-kbd">×</span> / 「重连会话」）是等价路径而非降级路径。
      </p>
    </div>
  );
}
