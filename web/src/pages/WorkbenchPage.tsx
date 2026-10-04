import { useCallback, useEffect, useRef, useState } from 'react';
import { tasksApi } from '../api/tasks';
import { workbenchApi, type WorkspaceSummary } from '../api/workbench';
import { openTaskEventStream } from '../api/sse';
import type { TaskEvent, TaskSummary } from '../api/types';
import { Spinner, errorMessage } from '../components/ui';
import { WorkspacePicker } from '../components/workbench/WorkspacePicker';
import { FileTree } from '../components/workbench/FileTree';
import { CodeEditor, type EditorJumpApi } from '../components/workbench/CodeEditor';
import { TerminalPane } from '../components/workbench/TerminalPane';
import { GitPanel } from '../components/workbench/GitPanel';
import { WorkbenchLayout } from '../components/workbench/WorkbenchLayout';
import { PreviewPane } from '../components/workbench/PreviewPane';
import { PreviewPanel } from '../components/workbench/PreviewPanel'; // P1-A 实时预览窗：统一预览源协议
import { DispatchDialog, type DispatchContext } from '../components/workbench/DispatchDialog'; // P1-15 点击弹窗派改
import { WorkbenchDispatchDialog, type CodeDispatchContext } from '../components/workbench/WorkbenchDispatchDialog'; // P1 交互双件：代码区点击弹框派发
import { DocumentTree } from '../components/workbench/DocumentTree';
import { CommitGraph } from '../components/workbench/CommitGraph';
import { DiffView } from '../components/workbench/DiffView';
import { OutlinePanel } from '../components/workbench/OutlinePanel'; // P1-13 侧边定位
import { BackupRestorePanel } from '../components/workbench/BackupRestorePanel'; // P1-14 备份回滚

function newIdempotencyKey(): string {
  if (typeof crypto !== 'undefined' && 'randomUUID' in crypto) return crypto.randomUUID();
  return `idem-${Date.now()}`;
}

const WORKSPACE_KEY = 'fy.workbench.workspace';

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

  // P1-15 点击弹窗派改：预览元素点击 → 弹窗（选中内容预填）；结果回填预览。
  const [dispatchCtx, setDispatchCtx] = useState<DispatchContext | null>(null);
  const [dispatchResult, setDispatchResult] = useState<string | null>(null);
  // P1 交互双件：代码区「派发给 Agent」上下文（选中片段/光标行），独立组件最小接线。
  const [codeDispatchCtx, setCodeDispatchCtx] = useState<CodeDispatchContext | null>(null);
  // P1-13 侧边定位：OutlinePanel ↔ CodeEditor 的命令式跳转句柄。
  const editorApiRef = useRef<EditorJumpApi | null>(null);
  // P1-14 备份回滚：回滚写盘成功后递增，强制 CodeEditor 重挂载以重新读盘。
  const [editorReloadKey, setEditorReloadKey] = useState(0);

  useEffect(() => () => streamRef.current?.close(), []);

  const loadWorkspaces = useCallback(async () => {
    try {
      const r = await workbenchApi.listWorkspaces();
      setWorkspaces(r.items);
      setWorkspaceId((cur) => {
        if (cur && r.items.some((w) => w.id === cur)) return cur;
        const remembered = readStoredWorkspace();
        if (remembered && r.items.some((w) => w.id === remembered)) return remembered;
        // The remembered workspace is gone (deleted or belongs to another
        // owner): drop the stale id so it cannot resurrect later.
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

  async function cancel() {
    if (!task) return;
    try {
      const t = await tasksApi.cancel(task.id);
      setTask(t);
      streamRef.current?.close();
    } catch (e) {
      setError(errorMessage(e));
    }
  }

  return (
    <>
      <div className="page-head"><h2>任务工作台</h2></div>

      <div className="card">
        <div className="field">
          <label htmlFor="goal">任务目标</label>
          <textarea
            id="goal"
            value={goal}
            onChange={(e) => setGoal(e.target.value)}
            placeholder="描述要完成的工程/调研任务"
          />
        </div>
        <button className="primary" onClick={() => void createTask()} disabled={loading || !goal.trim()}>
          {loading ? '创建中…' : '创建任务（幂等）'}
        </button>
        {error && <div className="error-text" role="alert">{error}</div>}
      </div>

      {task && (
        <div className="card">
          <div className="row spread">
            <strong>{task.goal}</strong>
            <span className="badge accent">{task.state}</span>
          </div>
          <div className="muted" style={{ margin: '0.4rem 0' }}>
            阶段 {task.stage} · 步数 {task.steps}/{task.max_steps} · 深度 {task.depth} · 幂等键 {task.idempotency_key}
          </div>
          {task.failure && <div className="notice danger">{task.failure}</div>}
          {!['succeeded', 'failed', 'cancelled'].includes(task.state) && (
            <button className="danger small" onClick={() => void cancel()}>取消任务</button>
          )}
          <h4>事件流（SSE）</h4>
          {events.length === 0 ? (
            <Spinner label="等待事件…" />
          ) : (
            <ul>
              {events.map((e, i) => (
                <li key={i}>
                  <code>{e.type}</code> · {new Date(e.at).toLocaleTimeString('zh-CN')}
                </li>
              ))}
            </ul>
          )}
        </div>
      )}

      <div className="page-head"><h2>工程代码工作台</h2></div>
      {wsError && <div className="error-text" role="alert">{wsError}</div>}
      <WorkspacePicker
        workspaces={workspaces}
        selectedId={workspaceId}
        onSelect={selectWorkspace}
        onChanged={() => void loadWorkspaces()}
      />

      {/* A 布局骨架（P1-02）：左代码/文件区 + 右预览窗可拖拽双栏，底部终端停靠。
          未选择工作区时三区仍渲染占位内容，布局与拖拽/折叠交互始终可用。 */}
      <WorkbenchLayout
        left={
          workspaceId ? (
            <>
              <FileTree workspaceId={workspaceId} selectedPath={selectedFile} onSelectFile={setSelectedFile} />
              {/* P1-13 侧边定位：大纲/搜索面板，通过 editorApiRef 驱动编辑器跳转 */}
              <OutlinePanel workspaceId={workspaceId} path={selectedFile} editorApiRef={editorApiRef} />
              {/* P1-14：key 变化触发编辑器重新读盘，回滚后缓冲区与磁盘同步 */}
              <CodeEditor key={editorReloadKey} workspaceId={workspaceId} path={selectedFile} editorApiRef={editorApiRef} onDispatch={setCodeDispatchCtx} />
              <GitPanel workspaceId={workspaceId} />
            </>
          ) : (
            <div className="card"><div className="muted">代码/文件区：注册并选择一个工作区后，文件树、编辑器与差异审查即可操作。</div></div>
          )
        }
        right={
          <>
            {/* P1-A 实时预览窗：已登记预览源（static html/md / process）+ 热刷新 */}
            <PreviewPanel workspaceId={workspaceId} path={selectedFile} />
            <PreviewPane
              onElementDispatch={setDispatchCtx}
              dispatchResult={dispatchResult}
            />
          </>
        }
        bottom={
          workspaceId ? (
            <TerminalPane workspaceId={workspaceId} />
          ) : (
            <div className="card"><div className="muted">终端：注册并选择一个工作区后可启动真实 PTY 会话。</div></div>
          )
        }
      />

      {workspaceId && <DocumentTree workspaceId={workspaceId} />}

      {/* P1-11 diff 可视化：独立面板，自含 git 工作区名称输入 */}
      <DiffView />

      {/* P1-14 备份回滚：备份当前文件到 /api/stash，可回滚并做哈希一致性比对 */}
      <BackupRestorePanel
        workspaceId={workspaceId}
        path={selectedFile}
        onRolledBack={() => setEditorReloadKey((k) => k + 1)}
      />

      {/* P1-12 git 提交树图：消费 /api/git-repo 提交历史，点击节点联动 diff */}
      <CommitGraph />

      {/* P1-15 点击弹窗派改：统一走 19 号 ModelBinding 链路 */}
      {dispatchCtx && (
        <DispatchDialog
          context={dispatchCtx}
          onClose={() => setDispatchCtx(null)}
          onApplyResult={setDispatchResult}
        />
      )}

      {/* P1 交互双件：代码区点击弹框派发（团队成员/任务管线真实端点，独立组件） */}
      {codeDispatchCtx && (
        <WorkbenchDispatchDialog
          context={codeDispatchCtx}
          onClose={() => setCodeDispatchCtx(null)}
        />
      )}
    </>
  );
}
