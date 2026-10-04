/**
 * P1 交互双件 · 任务二：代码区「点击弹框派发」对话框（独立组件，最小接线）。
 *
 * 只走真实端点，绝不伪造成功（FROZEN_CONTRACT §1 / §11）：
 *   通道 A（团队成员 Agent）：
 *     1. GET  /api/teams                       —— 团队枚举（member_roles）
 *     2. GET  /api/teams/{id}                  —— 成员快照（真实角色/状态/冻结模型）
 *     3. POST /api/teams/{id}/start            —— 团队仍为 draft 时启动并冻结 ModelBinding
 *     4. POST /api/teams/{id}/members/execute  —— 经冻结绑定执行，回执含 run_batch/批次号
 *   通道 B（任务管线）：
 *     POST /api/tasks —— 幂等建任务（真实管线），回执 task_id，GET /api/tasks/{id} 轮询状态。
 *
 * 诚实说明（19 号规格）：
 *   - 「指定具体模型」不在本对话框 v1 范围：成员执行模型由其已冻结的 ModelBinding
 *     决定；如需更换请在团队画布改绑后重新派发。回执如实回报 requested/effective。
 *   - workbench orchestrator 端点（/api/workbench/orchestrator/*）只做租约/接管校验，
 *     无自由文本执行通道，故不作为执行者选项。
 */
import { useEffect, useRef, useState } from 'react';
import { teamsApi, type TeamSnapshot, type TeamSummary } from '../../api/teams';
import { tasksApi } from '../../api/tasks';
import type { ConversationMode } from '../../api/types';
import { ApiError } from '../../api/client';
import { errorMessage } from '../ui';

export interface CodeDispatchContext {
  /** 目标文件相对路径 */
  path: string;
  /** 选中的代码片段（未选中时为光标所在行或文件开头摘要） */
  snippet: string;
  /** 片段起始行（1-based）；无法定位时为 null */
  line: number | null;
}

type ExecutorKind = 'member' | 'task';

// 与后端 TaskCreate 的 mode Literal 一致（src/find_yourself/api/schemas.py:51）。
const TASK_MODES: ConversationMode[] = ['explore', 'research', 'engineering', 'creative', 'listen'];
const TERMINAL_STATES = ['succeeded', 'failed', 'cancelled'];

function newIdempotencyKey(): string {
  if (typeof crypto !== 'undefined' && 'randomUUID' in crypto) return crypto.randomUUID();
  return `idem-${Date.now()}-${Math.random().toString(36).slice(2)}`;
}

/** 失败时展示错误 envelope 原文（§1 统一 envelope），不做美化改写。 */
function errorEnvelopeText(e: unknown): string {
  if (e instanceof ApiError) {
    return JSON.stringify({ status: e.status, error: e.body }, null, 2);
  }
  return errorMessage(e);
}

function buildPrompt(requirement: string, ctx: CodeDispatchContext): string {
  const loc = ctx.line ? `${ctx.path} 第 ${ctx.line} 行` : ctx.path;
  return [
    requirement.trim(),
    '--- 目标代码（请基于以下片段执行更改） ---',
    `位置：${loc}`,
    ctx.snippet,
  ].join('\n');
}

interface MemberReceipt {
  kind: 'member';
  team_id: string;
  role: string;
  run_batch: number;
  session_id: string;
  requested_model: string;
  effective_model: string;
  settled_usd: string;
  raw: string;
}
interface TaskReceipt {
  kind: 'task';
  task_id: string;
  raw: string;
}
type Receipt = MemberReceipt | TaskReceipt;

export function WorkbenchDispatchDialog(props: {
  context: CodeDispatchContext;
  onClose: () => void;
}) {
  const { context, onClose } = props;

  const [executorKind, setExecutorKind] = useState<ExecutorKind>('member');
  const [teams, setTeams] = useState<TeamSummary[] | null>(null);
  const [teamsError, setTeamsError] = useState<string | null>(null);
  const [selectedTeamId, setSelectedTeamId] = useState('');
  const [teamSnap, setTeamSnap] = useState<TeamSnapshot | null>(null);
  const [snapError, setSnapError] = useState<string | null>(null);
  const [selectedRole, setSelectedRole] = useState('');
  const [taskMode, setTaskMode] = useState<ConversationMode>('engineering');
  const [requirement, setRequirement] = useState('');
  const [validationError, setValidationError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [phase, setPhase] = useState('');
  const [submitError, setSubmitError] = useState<string | null>(null);
  const [receipt, setReceipt] = useState<Receipt | null>(null);
  const [taskStatus, setTaskStatus] = useState<{ state: string; stage: string } | null>(null);
  const [statusDetail, setStatusDetail] = useState<string | null>(null);
  const requirementRef = useRef<HTMLTextAreaElement | null>(null);

  useEffect(() => {
    requirementRef.current?.focus();
  }, []);

  // 团队枚举：GET /api/teams（真实数据，空则如实提示）。
  useEffect(() => {
    let alive = true;
    teamsApi
      .list()
      .then((res) => {
        if (!alive) return;
        setTeams(res.items);
        if (res.items[0]) setSelectedTeamId(res.items[0].id);
      })
      .catch((e) => {
        if (alive) setTeamsError(errorMessage(e));
      });
    return () => {
      alive = false;
    };
  }, []);

  // 选定团队后拉成员快照：真实角色/状态/冻结模型（member_roles 只是字符串）。
  useEffect(() => {
    if (!selectedTeamId) {
      setTeamSnap(null);
      setSnapError(null);
      return;
    }
    let alive = true;
    setSnapError(null);
    teamsApi
      .get(selectedTeamId)
      .then((s) => {
        if (!alive) setTeamSnap(null);
        else setTeamSnap(s);
      })
      .catch((e) => {
        if (alive) setSnapError(errorMessage(e));
      });
    return () => {
      alive = false;
    };
  }, [selectedTeamId]);

  // 任务回执轮询：GET /api/tasks/{id}，终态即停。注意后端序列化字段为 status。
  useEffect(() => {
    if (receipt?.kind !== 'task') return;
    let stopped = false;
    let handle: ReturnType<typeof setInterval> | null = null;
    const tick = async () => {
      try {
        const t = await tasksApi.get(receipt.task_id);
        if (stopped) return;
        const raw = t as unknown as { state?: string; status?: string; stage?: string };
        const state = String(raw.state ?? raw.status ?? '未知');
        setTaskStatus({ state, stage: String(raw.stage ?? '') });
        if (TERMINAL_STATES.includes(state)) {
          stopped = true;
          if (handle) clearInterval(handle);
        }
      } catch {
        /* 瞬时失败保持轮询，不伪造状态 */
      }
    };
    handle = setInterval(() => void tick(), 3000);
    void tick();
    return () => {
      stopped = true;
      if (handle) clearInterval(handle);
    };
  }, [receipt]);

  const selectedTeam = teams?.find((t) => t.id === selectedTeamId) ?? null;
  const teamState = teamSnap?.team.state ?? selectedTeam?.state ?? '';
  const roleOptions =
    teamSnap?.members.map((m) => ({
      value: m.role,
      label: `${m.role}（${m.title || '成员'} · 状态 ${m.state} · 模型 ${m.requested_model || '未知'}）`,
    })) ??
    selectedTeam?.member_roles.map((r) => ({ value: r, label: r })) ??
    [];
  const selectedMember = teamSnap?.members.find((m) => m.role === selectedRole) ?? null;

  async function submit() {
    setValidationError(null);
    setSubmitError(null);
    setStatusDetail(null);
    if (!requirement.trim()) {
      setValidationError('请输入需求描述（必填）');
      requirementRef.current?.focus();
      return;
    }
    if (executorKind === 'member' && !selectedRole) {
      setValidationError('请选择一个团队成员角色');
      return;
    }
    setSubmitting(true);
    try {
      const prompt = buildPrompt(requirement, context);
      if (executorKind === 'member' && selectedTeam) {
        // draft 团队先启动（冻结成员 ModelBinding），再经冻结绑定执行。
        let version = teamSnap?.team.version ?? selectedTeam.version;
        if (teamState === 'draft') {
          setPhase('启动团队并冻结 ModelBinding…');
          const started = await teamsApi.start(selectedTeam.id, version);
          setTeamSnap(started);
          version = started.team.version;
        }
        setPhase('经成员冻结绑定执行…');
        const r = await teamsApi.executeMember(selectedTeam.id, {
          role: selectedRole,
          prompt,
          max_tokens: 1024,
        });
        setReceipt({
          kind: 'member',
          team_id: selectedTeam.id,
          role: selectedRole,
          run_batch: r.run_batch,
          session_id: r.session_id,
          requested_model: r.requested_model,
          effective_model: r.effective_model,
          settled_usd: r.settled_usd,
          raw: JSON.stringify(r, null, 2),
        });
      } else {
        setPhase('创建任务（幂等）…');
        const r = await tasksApi.create({
          goal: prompt,
          mode: taskMode,
          idempotency_key: newIdempotencyKey(),
        });
        const raw = r as unknown as { state?: string; status?: string; stage?: string };
        setTaskStatus({
          state: String(raw.state ?? raw.status ?? '未知'),
          stage: String(raw.stage ?? ''),
        });
        setReceipt({
          kind: 'task',
          task_id: r.id,
          raw: JSON.stringify(r, null, 2),
        });
      }
    } catch (e) {
      setSubmitError(errorEnvelopeText(e));
    } finally {
      setSubmitting(false);
      setPhase('');
    }
  }

  // 「查看状态」：成员通道拉团队事件流；任务通道立即刷新一次任务状态。
  async function checkStatus() {
    if (!receipt) return;
    try {
      if (receipt.kind === 'member') {
        const ev = await teamsApi.events(receipt.team_id, 0);
        const latest = ev.items[ev.items.length - 1];
        setStatusDetail(
          latest
            ? `最新事件 seq=${latest.seq} · ${latest.event_type} · ${new Date(latest.created_at).toLocaleString('zh-CN')}（共 ${ev.count} 条）`
            : `暂无事件（共 ${ev.count} 条）`,
        );
      } else {
        const t = await tasksApi.get(receipt.task_id);
        const raw = t as unknown as { state?: string; status?: string; stage?: string };
        const state = String(raw.state ?? raw.status ?? '未知');
        setTaskStatus({ state, stage: String(raw.stage ?? '') });
        setStatusDetail(`任务 ${receipt.task_id} 当前：${state} · 阶段 ${String(raw.stage ?? '')}`);
      }
    } catch (e) {
      setStatusDetail(`状态查询失败：${errorMessage(e)}`);
    }
  }

  return (
    <div
      className="wb-dispatch-overlay"
      data-testid="wb-dispatch-overlay"
      role="dialog"
      aria-modal="true"
      aria-label="派发给 Agent"
      onClick={(e) => {
        if (e.target === e.currentTarget && !submitting) onClose();
      }}
    >
      <div className="card wb-dispatch">
        <div className="row spread">
          <strong>派发给 Agent（代码区）</strong>
          <button
            className="small"
            data-testid="wb-dispatch-close"
            onClick={onClose}
            disabled={submitting}
            aria-label="关闭派发弹窗"
          >
            ✕
          </button>
        </div>

        <div className="field">
          <label>目标代码（自动引用）</label>
          <div className="notice" data-testid="wb-dispatch-context" style={{ maxHeight: '7rem', overflow: 'auto' }}>
            <div className="muted small">
              {context.path}
              {context.line ? ` · 第 ${context.line} 行` : ''}
            </div>
            <code className="wb-dispatch-snippet small" style={{ margin: '0.2rem 0 0' }}>
              {context.snippet || '（无选中片段）'}
            </code>
          </div>
        </div>

        <div className="field">
          <label htmlFor="wb-dispatch-requirement">需求描述</label>
          <textarea
            id="wb-dispatch-requirement"
            ref={requirementRef}
            data-testid="wb-dispatch-requirement"
            value={requirement}
            onChange={(e) => setRequirement(e.target.value)}
            placeholder="例如：把这个函数抽出重试逻辑，并补充边界条件"
            disabled={submitting}
          />
          {validationError && (
            <div className="error-text" role="alert" data-testid="wb-dispatch-validation">
              {validationError}
            </div>
          )}
        </div>

        <div className="field">
          <label htmlFor="wb-dispatch-executor-kind">执行者</label>
          <select
            id="wb-dispatch-executor-kind"
            data-testid="wb-dispatch-executor-kind"
            value={executorKind}
            onChange={(e) => setExecutorKind(e.target.value as ExecutorKind)}
            disabled={submitting}
          >
            <option value="member">团队成员（Agent）</option>
            <option value="task">任务管线（按模式建任务）</option>
          </select>
        </div>

        {executorKind === 'member' && (
          <>
            <div className="field">
              <label htmlFor="wb-dispatch-team">团队</label>
              {teamsError && (
                <div className="error-text" role="alert">团队列表加载失败：{teamsError}</div>
              )}
              {!teamsError && !teams && <span className="muted small">加载团队列表…</span>}
              {teams && (
                <select
                  id="wb-dispatch-team"
                  data-testid="wb-dispatch-team"
                  value={selectedTeamId}
                  onChange={(e) => {
                    setSelectedTeamId(e.target.value);
                    setSelectedRole('');
                  }}
                  disabled={submitting || teams.length === 0}
                >
                  {teams.length === 0 && <option value="">（暂无可用团队）</option>}
                  {teams.map((t) => (
                    <option key={t.id} value={t.id}>
                      {t.name}（{t.mode} · {t.state}）
                    </option>
                  ))}
                </select>
              )}
              {teams && teams.length === 0 && (
                <div className="muted small" data-testid="wb-dispatch-no-team">
                  暂无可用团队：请先在协作画布创建团队并配置成员/模型绑定，再用本通道；
                  当前可改用「任务管线」通道。
                </div>
              )}
            </div>

            {selectedTeam && (
              <div className="field">
                <label htmlFor="wb-dispatch-role">成员角色</label>
                {snapError && (
                  <div className="error-text" role="alert">成员快照加载失败：{snapError}</div>
                )}
                <select
                  id="wb-dispatch-role"
                  data-testid="wb-dispatch-role"
                  value={selectedRole}
                  onChange={(e) => setSelectedRole(e.target.value)}
                  disabled={submitting || roleOptions.length === 0}
                >
                  {roleOptions.length === 0 && <option value="">（该团队暂无成员角色）</option>}
                  {roleOptions.map((r) => (
                    <option key={r.value} value={r.value}>{r.label}</option>
                  ))}
                </select>
                {selectedMember && (
                  <div className="muted small" data-testid="wb-dispatch-model-note">
                    执行模型由该成员已冻结的 ModelBinding 决定（requested_model：
                    {selectedMember.requested_model || '未知'}）。本对话框不支持派发时临时指定
                    其他模型；如需更换该角色的模型，请在团队画布为其改绑后重新派发。执行回执
                    会如实回报 requested/effective 模型。
                  </div>
                )}
              </div>
            )}
          </>
        )}

        {executorKind === 'task' && (
          <div className="field">
            <label htmlFor="wb-dispatch-mode">任务模式</label>
            <select
              id="wb-dispatch-mode"
              data-testid="wb-dispatch-mode"
              value={taskMode}
              onChange={(e) => setTaskMode(e.target.value as ConversationMode)}
              disabled={submitting}
            >
              {TASK_MODES.map((m) => (
                <option key={m} value={m}>{m}</option>
              ))}
            </select>
            <div className="muted small" data-testid="wb-dispatch-model-note">
              任务管线通道不指定具体 agent 或模型：由后端按所选模式与策略编排
              （POST /api/tasks 真实建任务，幂等键防重）。
            </div>
          </div>
        )}

        <div className="row">
          <button
            className="primary"
            data-testid="wb-dispatch-submit"
            onClick={() => void submit()}
            disabled={
              submitting ||
              !requirement.trim() ||
              (executorKind === 'member' && !selectedRole)
            }
          >
            {submitting ? '提交中…' : '派发'}
          </button>
          {submitting && phase && (
            <span className="muted small" data-testid="wb-dispatch-phase">{phase}</span>
          )}
        </div>

        {submitError && (
          <div className="field">
            <label>失败回执（错误 envelope 原文）</label>
            <div className="error-text" role="alert" data-testid="wb-dispatch-error">
              <pre className="small" style={{ whiteSpace: 'pre-wrap', margin: 0 }}>{submitError}</pre>
            </div>
          </div>
        )}

        {receipt && (
          <div className="field" data-testid="wb-dispatch-receipt">
            <label>真实回执</label>
            <div className="notice">
              {receipt.kind === 'member' ? (
                <div className="small">
                  <div>通道：团队成员执行 · 团队 {receipt.team_id} · 角色 {receipt.role}</div>
                  <div>批次号 run_batch：{receipt.run_batch} · 会话 {receipt.session_id}</div>
                  <div>
                    请求模型 {receipt.requested_model} · 实际模型 {receipt.effective_model || '未知（提供方未回报路由）'}
                  </div>
                  <div>结算 {receipt.settled_usd} USD</div>
                </div>
              ) : (
                <div className="small">
                  <div>通道：任务管线 · task_id {receipt.task_id}</div>
                  <div>
                    当前状态 {taskStatus?.state ?? '…'}
                    {taskStatus?.stage ? ` · 阶段 ${taskStatus.stage}` : ''}（每 3 秒自动轮询，终态即停）
                  </div>
                </div>
              )}
              <pre className="small" style={{ whiteSpace: 'pre-wrap', margin: '0.3rem 0 0', maxHeight: '8rem', overflow: 'auto' }}>
                {receipt.raw}
              </pre>
            </div>
            <div className="row" style={{ marginTop: '0.4rem' }}>
              <button className="small" data-testid="wb-dispatch-status" onClick={() => void checkStatus()} disabled={submitting}>
                查看状态
              </button>
            </div>
            {statusDetail && (
              <div className="muted small" data-testid="wb-dispatch-status-detail">{statusDetail}</div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
