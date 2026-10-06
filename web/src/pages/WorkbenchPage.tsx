import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { tasksApi } from '../api/tasks';
import { workbenchApi, type WorkspaceSummary } from '../api/workbench';
import { openTaskEventStream } from '../api/sse';
import type { TaskEvent, TaskSummary } from '../api/types';
import type { FileContent } from '../api/workbench';
import { Spinner, errorMessage } from '../components/ui';
import { LineIcon } from '../components/ui/LineIcon';
import { WorkspacePicker } from '../components/workbench/WorkspacePicker';
import { FileTree } from '../components/workbench/FileTree';
import { CodeEditor, type EditorJumpApi } from '../components/workbench/CodeEditor';
import { GitPanel } from '../components/workbench/GitPanel';
import { WorkbenchLayout, readLayoutState, writeLayoutState, type LayoutState } from '../components/workbench/WorkbenchLayout';
import { PreviewPane } from '../components/workbench/PreviewPane';
import { PreviewPanel } from '../components/workbench/PreviewPanel';
import { DispatchDialog, type DispatchContext } from '../components/workbench/DispatchDialog';
import { WorkbenchDispatchDialog, type CodeDispatchContext } from '../components/workbench/WorkbenchDispatchDialog';
import { DocumentTree } from '../components/workbench/DocumentTree';
import { CommitGraph } from '../components/workbench/CommitGraph';
import { DiffView } from '../components/workbench/DiffView';
import { OutlinePanel } from '../components/workbench/OutlinePanel';
import { BackupRestorePanel } from '../components/workbench/BackupRestorePanel';
import { TerminalDock } from '../components/workbench/TerminalDock';
import { VerifyStatusCard } from '../components/workbench/VerifyStatusCard';
import { ConfirmDialog } from '../components/workbench/ConfirmDialog';
import { WbSection } from '../components/workbench/WbSection';
import { WbToastStack, useWbToasts } from '../components/workbench/WbToast';
import { CommandPalette } from '../components/workbench/CommandPalette';
import { matchBacktick, matchAccessKey, matchCommandPalette, mayTake } from '../components/workbench/wbKeys';
import './../styles/pages/workbench.css';

function newIdempotencyKey(): string {
  if (typeof crypto !== 'undefined' && 'randomUUID' in crypto) return crypto.randomUUID();
  return `idem-${Date.now()}`;
}

const WORKSPACE_KEY = 'fy.workbench.workspace';

/** 任务状态 → 九档 + 中文（状态 = 颜色 + 图标 + 文字，三通道）。 */
const TASK_TONE: Record<string, { tone: string; text: string; icon: Parameters<typeof LineIcon>[0]['name'] }> = {
  queued: { tone: 'waiting', text: '排队中', icon: 'clock' },
  running: { tone: 'running', text: '执行中', icon: 'refresh' },
  verifying: { tone: 'verifying', text: '验证中', icon: 'shield' },
  succeeded: { tone: 'complete', text: '已完成', icon: 'check' },
  failed: { tone: 'failed', text: '失败', icon: 'xCircle' },
  cancelled: { tone: 'paused', text: '已取消', icon: 'pause' },
  blocked: { tone: 'blocked', text: '阻塞', icon: 'alert' },
};

function taskTone(state: string) {
  return TASK_TONE[state] ?? { tone: 'idle', text: state, icon: 'info' as const };
}

/**
 * The selected workspace is persisted: a refresh must come back to the same
 * workspace, otherwise the file tree, the git panel and (above all) the
 * terminal reattach would silently land on an unrelated workspace and the
 * still-running server-side PTY would look lost.
 */
function readStoredWorkspace(): string | null {
  try {
    return window.localStorage.getItem(WORKSPACE_KEY);
  } catch {
    return null;
  }
}

function writeStoredWorkspace(id: string | null): void {
  try {
    if (id) window.localStorage.setItem(WORKSPACE_KEY, id);
    else window.localStorage.removeItem(WORKSPACE_KEY);
  } catch {
    /* private mode / quota — the selection just does not survive a refresh */
  }
}

export function WorkbenchPage() {
  const navigate = useNavigate();

  // Task slice (pre-existing behaviour).
  const [goal, setGoal] = useState('');
  const [task, setTask] = useState<TaskSummary | null>(null);
  const [events, setEvents] = useState<TaskEvent[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const streamRef = useRef<{ close: () => void } | null>(null);

  // Workbench slice (newly wired to the real API).
  const [workspaces, setWorkspaces] = useState<WorkspaceSummary[]>([]);
  const [workspaceId, setWorkspaceId] = useState<string | null>(null);
  const [selectedFile, setSelectedFile] = useState<string | null>(null);
  const [wsError, setWsError] = useState<string | null>(null);

  // P1-15 点击弹窗派改
  const [dispatchCtx, setDispatchCtx] = useState<DispatchContext | null>(null);
  const [dispatchResult, setDispatchResult] = useState<string | null>(null);
  const [codeDispatchCtx, setCodeDispatchCtx] = useState<CodeDispatchContext | null>(null);
  const editorApiRef = useRef<EditorJumpApi | null>(null);
  const [editorReloadKey, setEditorReloadKey] = useState(0);

  // 右区「验证状态卡」的真实数据源（绝不伪造）。
  const [sessionSnap, setSessionSnap] = useState<{
    sessionId: string | null;
    state: string | null;
    exitCode: number | null;
  } | null>(null);
  const [fileSnap, setFileSnap] = useState<FileContent | null>(null);
  const [fileReadAt, setFileReadAt] = useState<string | null>(null);
  const [terminalRestart, setTerminalRestart] = useState(0);

  // 破坏性动作：取消任务走模态
  const [cancelArmed, setCancelArmed] = useState(false);

  // 命令面板
  const [paletteOpen, setPaletteOpen] = useState(false);

  const toasts = useWbToasts();

  // 底部四区段展开态（与三区/坞共享同一份 fy.workbench.layout-a）。
  // 页面层是这一份状态的**唯一持有者与写入者**：三区宽度、坞高、坞折叠、四区段
  // 展开态同源，避免 WorkbenchLayout 内部再持一份导致互相覆盖。
  const [layoutState, setLayoutStateRaw] = useState<LayoutState>(readLayoutState);
  const setLayoutState = useCallback((updater: (s: LayoutState) => LayoutState) => {
    setLayoutStateRaw((s) => {
      const next = updater(s);
      writeLayoutState(next);
      return next;
    });
  }, []);
  const sections = layoutState.sections;
  const toggleSection = useCallback(
    (i: number) => {
      setLayoutState((s) => {
        const next = [...s.sections] as typeof s.sections;
        next[i] = !next[i];
        return { ...s, sections: next };
      });
    },
    [setLayoutState],
  );
  const sectionRefs = useRef<Array<HTMLDivElement | null>>([null, null, null, null]);
  const focusSection = useCallback(
    (i: number) => {
      setLayoutState((s) => {
        const next = [...s.sections] as typeof s.sections;
        next[i] = true;
        return { ...s, sections: next };
      });
      window.setTimeout(() => {
        const el = sectionRefs.current[i];
        el?.scrollIntoView({ block: 'nearest' });
        el?.querySelector<HTMLElement>('.wb-sec-bar')?.focus();
      }, 0);
    },
    [setLayoutState],
  );

  useEffect(() => () => streamRef.current?.close(), []);

  const loadWorkspaces = useCallback(async () => {
    try {
      const r = await workbenchApi.listWorkspaces();
      setWorkspaces(r.items);
      setWorkspaceId((cur) => {
        if (cur && r.items.some((w) => w.id === cur)) return cur;
        const remembered = readStoredWorkspace();
        if (remembered && r.items.some((w) => w.id === remembered)) return remembered;
        writeStoredWorkspace(null);
        return r.items[0]?.id ?? null;
      });
    } catch (e) {
      setWsError(errorMessage(e));
    }
  }, []);

  const selectWorkspace = useCallback((id: string) => {
    setWorkspaceId(id);
    writeStoredWorkspace(id);
  }, []);

  useEffect(() => {
    void loadWorkspaces();
  }, [loadWorkspaces]);

  // 读盘后回填哈希/版本供「验证状态卡」使用（数据来自真实 FileContent）。
  useEffect(() => {
    let alive = true;
    if (!workspaceId || !selectedFile) {
      setFileSnap(null);
      setFileReadAt(null);
      return;
    }
    void workbenchApi
      .readFile(workspaceId, selectedFile)
      .then((f) => {
        if (!alive) return;
        setFileSnap(f);
        setFileReadAt(new Date().toLocaleString('zh-CN'));
      })
      .catch(() => {
        if (!alive) return;
        setFileSnap(null);
        setFileReadAt(null);
      });
    return () => {
      alive = false;
    };
  }, [workspaceId, selectedFile, editorReloadKey]);

  async function createTask() {
    if (!goal.trim()) return;
    setLoading(true);
    setError(null);
    setEvents([]);
    try {
      const t = await tasksApi.create({ goal: goal.trim(), idempotency_key: newIdempotencyKey() });
      setTask(t);
      streamRef.current = openTaskEventStream(t.id, {
        onEvent: (e) => setEvents((ev) => [...ev, e]),
        onError: () => {},
      });
      const poll = setInterval(async () => {
        try {
          const fresh = await tasksApi.get(t.id);
          setTask(fresh);
          if (['succeeded', 'failed', 'cancelled'].includes(fresh.state)) {
            clearInterval(poll);
            streamRef.current?.close();
          }
        } catch {
          /* ignore transient */
        }
      }, 3000);
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setLoading(false);
    }
  }

  const doCancel = useCallback(async () => {
    if (!task) return;
    try {
      const t = await tasksApi.cancel(task.id);
      setTask(t);
      streamRef.current?.close();
      toasts.push({ tone: 'info', icon: 'info', text: `任务已取消（${t.id.slice(0, 8)}）。事件流保留可查。` });
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setCancelArmed(false);
    }
  }, [task, toasts]);

  // ---- 全局快捷键（§2）：IME 一律放行；可编辑区只放行带修饰键的组合 ----
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (matchCommandPalette(e)) {
        e.preventDefault();
        setPaletteOpen(true);
        return;
      }
      if (matchBacktick(e)) {
        e.preventDefault();
        setLayoutState((s) => ({ ...s, terminalCollapsed: !s.terminalCollapsed }));
        return;
      }
      const n = matchAccessKey(e);
      if (n !== null) {
        e.preventDefault();
        if (n === 'all') {
          setLayoutState((s) => ({ ...s, sections: [true, true, true, true] }));
        } else if (n >= 1 && n <= 4) {
          focusSection(n - 1);
        }
        return;
      }
      // Esc 逐级返退：先关命令面板
      if (e.key === 'Escape' && mayTake(e)) setPaletteOpen(false);
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [setLayoutState, focusSection]);

  const sectionDefs = useMemo(
    () => [
      { title: '差异审查', icon: 'flow' as const, hotkey: 'Ctrl+Shift+1' },
      { title: '文档树', icon: 'file' as const, hotkey: 'Ctrl+Shift+2' },
      { title: '提交图', icon: 'branch' as const, hotkey: 'Ctrl+Shift+3' },
      { title: '备份与回滚', icon: 'archive' as const, hotkey: 'Ctrl+Shift+4' },
    ],
    [],
  );

  const sectionBodies = [
    <DiffView key="diff" />,
    workspaceId ? (
      <DocumentTree key="doc" workspaceId={workspaceId} />
    ) : (
      <div className="ui-empty" key="doc">
        <LineIcon name="file" size={24} />
        <p className="ui-empty-title">还没有工作区</p>
        <p className="ui-empty-hint">注册一个受信任目录后，文档树才能列出条目。</p>
      </div>
    ),
    <CommitGraph key="cg" />,
    <BackupRestorePanel
      key="backup"
      workspaceId={workspaceId}
      path={selectedFile}
      onRolledBack={() => setEditorReloadKey((k) => k + 1)}
    />,
  ];

  const tone = task ? taskTone(task.state) : null;
  const editorSectionStatus = selectedFile ? (
    <span className="wb-sec-kbd muted small">{selectedFile.split('/').pop()}</span>
  ) : undefined;

  return (
    <div className="wb-shell">
      {/* ---------------------------------------------------------- 页首 */}
      <div className="page-head wb-page-head">
        <div>
          <nav className="wb-crumbs" aria-label="面包屑">
            工作台空间 <span aria-hidden="true">›</span> <b>任务工作台</b>
          </nav>
          <h2>任务工作台</h2>
        </div>
        <span className="ui-spacer" />
        <button type="button" className="ui-btn ui-btn--sm" onClick={() => setPaletteOpen(true)}>
          <LineIcon name="search" size={16} />
          搜索文件与命令
          <span className="ui-kbd">⌘K</span>
        </button>
      </div>

      {/* 工作区条（WorkspacePicker 迁入 wb-workspace-strip） */}
      <div className="wb-workspace-strip">
        <WorkspacePicker
          workspaces={workspaces}
          selectedId={workspaceId}
          onSelect={selectWorkspace}
          onChanged={() => void loadWorkspaces()}
        />
      </div>
      {wsError && (
        <div className="ui-error-text" role="alert">
          {wsError}
        </div>
      )}

      {/* ------------------------------------------------- 三区 + 坞（布局骨架） */}
      <WorkbenchLayout
        state={layoutState}
        onStateChange={setLayoutState}
        left={
          <>
            {/* L1 任务树 */}
            <section className="wb-tasktree" aria-label="任务树">
              <div className="wb-tasktree-form">
                <label className="ui-label" htmlFor="goal">
                  任务目标
                </label>
                <textarea
                  id="goal"
                  value={goal}
                  onChange={(e) => setGoal(e.target.value)}
                  onKeyDown={(e) => {
                    if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') {
                      e.preventDefault();
                      void createTask();
                    }
                  }}
                  placeholder="描述要完成的工程/调研任务（Ctrl+Enter 创建）"
                />
                <button
                  type="button"
                  className="ui-btn ui-btn--sm ui-btn--primary"
                  onClick={() => void createTask()}
                  disabled={loading || !goal.trim()}
                >
                  {loading ? '创建中…' : '创建任务（幂等）'}
                </button>
              </div>

              {error && (
                <div className="ui-error-text" role="alert">
                  {error}
                </div>
              )}

              {task ? (
                <article className="wb-task-card is-active" aria-label="当前任务">
                  <div className="ui-row">
                    <strong className="wb-task-goal">{task.goal}</strong>
                  </div>
                  <div className="ui-row">
                    <span className="ui-badge" data-tone={tone?.tone}>
                      <LineIcon name={tone!.icon} size={14} />
                      {tone!.text}
                    </span>
                  </div>
                  <div className="wb-task-kv">
                    <span>阶段 {task.stage}</span>
                    <span>
                      步数 {task.steps}/{task.max_steps}
                    </span>
                    <span>深度 {task.depth}</span>
                    <span title={task.idempotency_key}>幂等键 {task.idempotency_key.slice(0, 8)}…</span>
                  </div>
                  {task.failure && (
                    <div className="disp-err" role="alert">
                      {task.failure}
                    </div>
                  )}
                  {!['succeeded', 'failed', 'cancelled'].includes(task.state) && (
                    <div className="ui-row">
                      <button
                        type="button"
                        className="ui-btn ui-btn--sm ui-btn--danger"
                        onClick={() => setCancelArmed(true)}
                        disabled={cancelArmed}
                      >
                        取消任务…
                      </button>
                    </div>
                  )}
                  <div>
                    <p className="ui-label">事件流（SSE）</p>
                    {events.length === 0 ? (
                      <Spinner label="正在等待第一条事件" />
                    ) : (
                      <ul className="wb-task-events" role="log" aria-live="polite">
                        {events.map((e, i) => (
                          <li key={i}>
                            <code>{e.type}</code>
                            <span>{new Date(e.at).toLocaleTimeString('zh-CN')}</span>
                          </li>
                        ))}
                      </ul>
                    )}
                  </div>
                </article>
              ) : (
                <div className="ui-empty">
                  <LineIcon name="dispatch" size={24} />
                  <p className="ui-empty-title">还没有任务</p>
                  <p className="ui-empty-hint">
                    写下目标，点「创建任务」即可跑一次；重复点击不会重复执行（幂等键保护）。
                  </p>
                </div>
              )}
            </section>

            {/* L2 文件树 */}
            {workspaceId ? (
              <FileTree workspaceId={workspaceId} selectedPath={selectedFile} onSelectFile={setSelectedFile} />
            ) : (
              <div className="ui-empty">
                <LineIcon name="folder" size={24} />
                <p className="ui-empty-title">还没有工作区</p>
                <p className="ui-empty-hint">注册一个受信任目录，工作台才能读文件、起终端、连 git。</p>
              </div>
            )}

            {/* L3 大纲与文件内搜索 */}
            {workspaceId && (
              <OutlinePanel workspaceId={workspaceId} path={selectedFile} editorApiRef={editorApiRef} />
            )}
          </>
        }
        middle={
          <>
            {/* 中区 ① 编辑器 · 预览 */}
            <WbSection
              index={1}
              title="编辑器 · 预览"
              icon="code"
              hotkey="1"
              collapsed={false}
              onToggle={() => {}}
              statusSlot={editorSectionStatus}
            >
              {workspaceId ? (
                <>
                  <CodeEditor
                    key={editorReloadKey}
                    workspaceId={workspaceId}
                    path={selectedFile}
                    editorApiRef={editorApiRef}
                    onDispatch={setCodeDispatchCtx}
                  />
                  <PreviewPanel workspaceId={workspaceId} path={selectedFile} />
                  <PreviewPane onElementDispatch={setDispatchCtx} dispatchResult={dispatchResult} />
                </>
              ) : (
                <div className="ui-empty">
                  <LineIcon name="code" size={24} />
                  <p className="ui-empty-title">还没打开文件</p>
                  <p className="ui-empty-hint">从左侧文件树选一个文件，编辑器会在这里打开。</p>
                </div>
              )}
            </WbSection>

            {/* 中区 ② 差异审查 */}
            <WbSection
              index={2}
              title="差异审查"
              icon="flow"
              hotkey="2"
              collapsed={false}
              onToggle={() => {}}
            >
              <DiffView />
            </WbSection>

            {/* 中区 ③ 差异审查 / Git */}
            <WbSection index={3} title="Git 工作区" icon="branch" hotkey="3" collapsed={false} onToggle={() => {}}>
              {workspaceId ? (
                <GitPanel workspaceId={workspaceId} />
              ) : (
                <div className="ui-empty">
                  <LineIcon name="branch" size={24} />
                  <p className="ui-empty-title">还没有工作区</p>
                  <p className="ui-empty-hint">注册并选择工作区后，可查看分支、暂存与提交。</p>
                </div>
              )}
            </WbSection>
          </>
        }
        right={
          <>
            {/* ④ 验证状态卡（视觉重心，压轴） */}
            <VerifyStatusCard
              src={{ file: fileSnap, fileReadAt, terminal: sessionSnap, task }}
            />

            {/* ③ 放行闸门入口 */}
            <section className="ui-panel ui-panel--pad" aria-label="放行闸门">
              <div className="ui-panel-hd" style={{ padding: 0, border: 0, marginBottom: 'var(--ui-s-3)' }}>
                <span className="ui-panel-title">
                  <LineIcon name="approvals" size={16} /> 放行闸门
                </span>
              </div>
              <p className="wb-verify-foot">
                审批与放行的七步闸门在「审批中心」逐条展开；这里只给出入口与当前任务状态，
                不在外部确认前显示任何「已合并/已发布」。
              </p>
              <button
                type="button"
                className="ui-btn ui-btn--sm ui-btn--block"
                onClick={() => navigate('/approvals?from=/workbench')}
              >
                前往审批中心
              </button>
            </section>

            {/* ② 用量 */}
            <section className="ui-panel ui-panel--pad" aria-label="用量">
              <div className="ui-panel-hd" style={{ padding: 0, border: 0, marginBottom: 'var(--ui-s-3)' }}>
                <span className="ui-panel-title">
                  <LineIcon name="budget" size={16} /> 用量
                </span>
              </div>
              <div className="wb-task-kv">
                <span>任务步数 {task ? `${task.steps}/${task.max_steps}` : '暂无数据 · 来源不可得'}</span>
                <span>
                  终端退出码{' '}
                  {sessionSnap?.exitCode === null || sessionSnap?.exitCode === undefined
                    ? '暂无数据 · 来源不可得'
                    : sessionSnap.exitCode}
                </span>
              </div>
            </section>
          </>
        }
        bottom={
          workspaceId ? (
            <TerminalDock
              workspaceId={workspaceId}
              restartSignal={terminalRestart}
              onSessionChange={setSessionSnap}
              onSessionStopped={(info) =>
                toasts.push({
                  tone: 'info',
                  icon: 'terminal',
                  text: `会话已停止（${info.reason}）。输出会保留在视图里。`,
                  action: { label: '重新启动会话', run: () => setTerminalRestart((n) => n + 1) },
                })
              }
            />
          ) : (
            <div className="wb-term-empty">
              注册并选择一个工作区后，可在此启动真实 PTY 会话（多标签共存，关闭标签不杀会话）。
            </div>
          )
        }
      />

      {/* ------------------ 底部四区段：在 wb-layout-a 之外（§14-2 D4 裁决） ------------------ */}
      <div className="wb-sections">
        {sectionDefs.map((def, i) => (
          <div key={def.title} ref={(el) => { sectionRefs.current[i] = el; }}>
            <WbSection
              index={i + 1}
              title={def.title}
              icon={def.icon}
              hotkey={def.hotkey}
              collapsed={!sections[i]}
              onToggle={() => toggleSection(i)}
            >
              {sectionBodies[i]}
            </WbSection>
          </div>
        ))}
      </div>

      {/* ---------------------------------------------------------- 浮层 */}
      {dispatchCtx && (
        <DispatchDialog context={dispatchCtx} onClose={() => setDispatchCtx(null)} onApplyResult={setDispatchResult} />
      )}
      {codeDispatchCtx && (
        <WorkbenchDispatchDialog context={codeDispatchCtx} onClose={() => setCodeDispatchCtx(null)} />
      )}

      {cancelArmed && task && (
        <ConfirmDialog
          title="取消这个任务？"
          destructive
          confirmLabel="取消任务"
          body={
            <>
              任务 <code>{task.id.slice(0, 8)}</code> 当前处于「{taskTone(task.state).text}」。取消后不可恢复；
              已产生的事件会保留在事件流里供查看，不会被删除。
            </>
          }
          onConfirm={() => void doCancel()}
          onCancel={() => setCancelArmed(false)}
        />
      )}

      <CommandPalette
        open={paletteOpen}
        workspaceId={workspaceId}
        onClose={() => setPaletteOpen(false)}
        onSelectFile={(p) => {
          setSelectedFile(p);
          setPaletteOpen(false);
        }}
        onRun={(id) => {
          setPaletteOpen(false);
          if (id === 'toggle-dock') setLayoutState((s) => ({ ...s, terminalCollapsed: !s.terminalCollapsed }));
          else if (id === 'save-file') document.dispatchEvent(new CustomEvent('wb:save-file'));
          else if (id === 'reload-tree') document.dispatchEvent(new CustomEvent('wb:reload-tree'));
          else if (id === 'refresh-git') document.dispatchEvent(new CustomEvent('wb:refresh-git'));
          else if (id.startsWith('section-')) focusSection(Number(id.slice(8)) - 1);
          else if (id === 'goto-approvals') navigate('/approvals?from=/workbench');
          else if (id === 'goto-dispatch') navigate('/agent-dispatch?from=/workbench');
          else if (id === 'goto-chat-debug') navigate('/chat-debug?from=/workbench');
          else if (id === 'goto-canvas') navigate('/canvas?from=/workbench');
        }}
      />

      <WbToastStack items={toasts.toasts} onDismiss={toasts.dismiss} />
    </div>
  );
}
