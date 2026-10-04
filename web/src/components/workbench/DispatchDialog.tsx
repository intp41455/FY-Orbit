/**
 * P1-15「点击弹窗派改」弹窗（统一走 19 号 ModelBinding 链路）。
 *
 * 所有网络请求都走 19 号团队/绑定 API，网络面板可直接观测：
 *   1. GET  /api/teams/catalog                 —— 模型/绑定选择器可选项（ModelBinding 可选项）
 *   2. GET  /api/teams                         —— 复用已存在的派改团队（避免重复建团）
 *   3. POST /api/teams                         —— 创建派改团队（default_binding = 所选模型）
 *   4. POST /api/teams/{id}/start              —— 启动团队，冻结成员 ModelBinding（frozen=True）
 *   5. POST /api/teams/{id}/members/execute    —— 经冻结绑定走 gateway 推理，返回改写结果
 *
 * 真实模型不可用时由后端侧的本地确定性提供方承接（stub 模式）：
 * 请求仍真实经过 ModelBinding 选择的链路（绑定→网关→提供方），不在此层伪造结果。
 */
import { useEffect, useMemo, useRef, useState } from 'react';
import { teamsApi, type ModelOption, type TeamSummary } from '../../api/teams';
import { errorMessage } from '../ui';

export interface DispatchContext {
  /** 触发派改的来源位置描述，如 "预览窗 · 标题段落" */
  source: string;
  /** 选中内容/元素文本，作为改写上下文预填 */
  text: string;
}

/** 派改专用团队名：复用判定依据（同属 owner，不会与他team冲突）。 */
const DISPATCH_TEAM_NAME = 'workbench-dispatch';
const DISPATCH_ROLE = 'rewriter';

interface DispatchResult {
  text: string;
  requested_model: string;
  effective_model: string;
  team_id: string;
}

export function DispatchDialog(props: {
  context: DispatchContext;
  onClose: () => void;
  /** 结果回填预览（P1-15 验收口径：结果回填预览） */
  onApplyResult?: (text: string) => void;
}) {
  const { context, onClose, onApplyResult } = props;

  const [models, setModels] = useState<ModelOption[] | null>(null);
  const [catalogError, setCatalogError] = useState<string | null>(null);
  const [selectedModel, setSelectedModel] = useState('');
  const [instruction, setInstruction] = useState('');
  const [validationError, setValidationError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [phase, setPhase] = useState('');
  const [submitError, setSubmitError] = useState<string | null>(null);
  const [result, setResult] = useState<DispatchResult | null>(null);
  const instructionRef = useRef<HTMLTextAreaElement | null>(null);

  useEffect(() => {
    let alive = true;
    teamsApi
      .catalog()
      .then((c) => {
        if (!alive) return;
        // 只列出真正可用的绑定项：凭据已配置 + 单价已知（19 §3：界面不能随意
        // 填写不存在的"可用"模型）。
        const usable = c.models.filter(
          (m) => m.credential_configured && m.pricing_status === 'known',
        );
        setModels(usable);
        const preferred =
          usable.find((m) => m.model_id === 'mock-deterministic') ?? usable[0];
        if (preferred) {
          setSelectedModel(`${preferred.provider_id}::${preferred.model_id}`);
        }
      })
      .catch((e) => {
        if (alive) setCatalogError(errorMessage(e));
      });
    return () => {
      alive = false;
    };
  }, []);

  useEffect(() => {
    instructionRef.current?.focus();
  }, []);

  const selectedOption = useMemo(() => {
    if (!models) return null;
    return models.find((m) => `${m.provider_id}::${m.model_id}` === selectedModel) ?? null;
  }, [models, selectedModel]);

  async function ensureDispatchTeam(model: ModelOption): Promise<TeamSummary> {
    const existing = await teamsApi.list();
    const found = existing.items.find((t) => t.name === DISPATCH_TEAM_NAME);
    if (found) return found;
    setPhase('创建派改团队…');
    const snap = await teamsApi.create({
      name: DISPATCH_TEAM_NAME,
      mode: 'system_managed',
      members: [
        {
          role: DISPATCH_ROLE,
          title: '派改执行器',
          goal: '根据改写指令对选中内容执行派改（rewrite）',
        },
      ],
      default_binding: { provider_id: model.provider_id, model_id: model.model_id },
      reason: 'P1-15 workbench dispatch team',
    });
    return {
      id: snap.team.id,
      name: snap.team.name,
      mode: snap.team.mode,
      state: snap.team.state,
      version: snap.team.version,
      plan_version: snap.team.plan_version,
      root_task_id: snap.team.root_task_id,
      member_roles: snap.members.map((m) => m.role),
    };
  }

  async function submit() {
    setValidationError(null);
    setSubmitError(null);
    if (!instruction.trim()) {
      setValidationError('请输入改写指令（必填）');
      instructionRef.current?.focus();
      return;
    }
    if (!selectedOption) {
      setValidationError('请选择一个模型绑定（ModelBinding）');
      return;
    }
    setSubmitting(true);
    try {
      const team = await ensureDispatchTeam(selectedOption);
      // 团队刚创建仍是 draft：start 会为成员冻结一份 ModelBinding（19 §3），
      // 之后的 execute 必须真实经过这份冻结绑定。
      if (team.state === 'draft') {
        setPhase('启动团队并冻结 ModelBinding…');
        const snap = await teamsApi.start(team.id, team.version);
        team.state = snap.team.state;
      }
      const prompt = [
        `改写指令：${instruction.trim()}`,
        '--- 待改写内容（来自工作台预览/代码区选中元素） ---',
        `来源：${context.source}`,
        context.text,
        // 每次派改唯一：预算预留幂等键按 prompt 哈希派生，重复内容也须各占各的。
        `请求编号：req-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
      ].join('\n');
      setPhase('经 ModelBinding 链路执行派改…');
      const r = await teamsApi.executeMember(team.id, {
        role: DISPATCH_ROLE,
        prompt,
        max_tokens: 512,
      });
      setResult({
        text: r.text,
        requested_model: r.requested_model,
        effective_model: r.effective_model,
        team_id: team.id,
      });
      onApplyResult?.(r.text);
    } catch (e) {
      setSubmitError(errorMessage(e));
    } finally {
      setSubmitting(false);
      setPhase('');
    }
  }

  return (
    <div
      className="dispatch-dialog-overlay"
      data-testid="dispatch-dialog"
      role="dialog"
      aria-modal="true"
      aria-label="派改"
      onClick={(e) => {
        if (e.target === e.currentTarget && !submitting) onClose();
      }}
    >
      <div className="card dispatch-dialog">
        <div className="row spread">
          <strong>派改（Rewrite）</strong>
          <button
            className="small"
            data-testid="dispatch-close"
            onClick={onClose}
            disabled={submitting}
            aria-label="关闭派改弹窗"
          >
            ✕
          </button>
        </div>

        <div className="field">
          <label>选中内容（预填上下文）</label>
          <div className="notice" data-testid="dispatch-context" style={{ maxHeight: '6rem', overflow: 'auto' }}>
            <div className="muted small">{context.source}</div>
            <pre className="small" style={{ whiteSpace: 'pre-wrap', margin: '0.2rem 0 0' }}>
              {context.text}
            </pre>
          </div>
        </div>

        <div className="field">
          <label htmlFor="dispatch-instruction">改写指令</label>
          <textarea
            id="dispatch-instruction"
            ref={instructionRef}
            value={instruction}
            onChange={(e) => setInstruction(e.target.value)}
            placeholder="例如：把这段话改写得更简洁，并保留关键事实"
            disabled={submitting}
          />
          {validationError && (
            <div className="error-text" role="alert" data-testid="dispatch-validation">
              {validationError}
            </div>
          )}
        </div>

        <div className="field">
          <label htmlFor="dispatch-model">模型绑定（ModelBinding 可选项）</label>
          {catalogError && (
            <div className="error-text" role="alert">
              绑定目录加载失败：{catalogError}
            </div>
          )}
          {!catalogError && !models && <span className="muted small">加载绑定目录…</span>}
          {models && (
            <select
              id="dispatch-model"
              data-testid="dispatch-model-select"
              value={selectedModel}
              onChange={(e) => setSelectedModel(e.target.value)}
              disabled={submitting || models.length === 0}
            >
              {models.length === 0 && <option value="">（无可用绑定）</option>}
              {models.map((m) => (
                <option key={`${m.provider_id}::${m.model_id}`} value={`${m.provider_id}::${m.model_id}`}>
                  {m.provider_id} / {m.model_id}
                  {m.synthetic ? '（本地确定性提供方）' : ''}
                </option>
              ))}
            </select>
          )}
          {selectedOption && (
            <div className="muted small" data-testid="dispatch-binding-note">
              绑定端点：{selectedOption.endpoint_ref || '（随团队启动冻结）'} · 单价已知 ·
              凭据引用 {selectedOption.credential_ref}
            </div>
          )}
        </div>

        <div className="row">
          <button
            className="primary"
            data-testid="dispatch-submit"
            onClick={() => void submit()}
            disabled={submitting || !models || models.length === 0}
          >
            {submitting ? '提交中…' : '提交派改'}
          </button>
          {submitting && phase && (
            <span className="muted small" data-testid="dispatch-phase">
              {phase}
            </span>
          )}
        </div>

        {submitError && (
          <div className="error-text" role="alert" data-testid="dispatch-error">
            {submitError}
          </div>
        )}

        {result && (
          <div className="field" data-testid="dispatch-result">
            <label>派改结果（已回填预览窗）</label>
            <div className="notice">
              <pre className="small" style={{ whiteSpace: 'pre-wrap', margin: 0 }}>
                {result.text}
              </pre>
            </div>
            <div className="muted small">
              团队 {result.team_id} · 请求模型 {result.requested_model} · 实际模型{' '}
              {result.effective_model}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
