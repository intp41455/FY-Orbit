// 多Agent 编排台：前置体检 → 派单 → 逐步时间线。
//
// 诚实约定（沿用HubPage 的三条，这里再加两条，都是踩过坑才写下来的）：
//  1. **降级必须可见**。推理型模型被 max_tokens 截断时，后端会降级返回
//     reasoning_content，此时 `ok=true` 且 `output.text` 有内容 —— 光看
//     成功标记和文本**完全看不出**拿到的是思考过程而不是答案。信号在
//     `step.meta.degraded_reason` 里，页面必须原样渲染，不能吞。
//  2. **派单去向必须有名**。pipeline 走显式分支时可能只拿得到 connection_id；
//     查不到名字就显示「未命名连接（id 前 8 位）」，不编一个名字上去。
//  3. **部分失败就是部分失败**。4 步成功 1 步失败时，顶层不能说「已完成」。
//     成功数/失败数原样展示，失败步的错误整段摊开。
import { useCallback, useEffect, useMemo, useState } from 'react';
import { hubApi, HUB_ORCHESTRATE_MODE_LABEL } from '../../api/hub';
import type {
  HubConnection,
  HubOrchestrateMode,
  HubOrchestratePayload,
  HubOrchestratePreflight,
  HubOrchestrationResult,
  HubPipelinePayload,
  HubPipelineStage,
  HubStepResult,
} from '../../api/hub';
import { errorMessage } from '../ui';
import { LineIcon } from '../ui/LineIcon';
import { useBase } from '../../hooks/useAutosave';

/** 从 output 里挖出可读文本；挖不到就返回 null，让调用方显示 JSON。 */
function outputText(output: unknown): string | null {
  if (typeof output === 'string') return output.trim() ? output : null;
  if (output && typeof output === 'object') {
    const text = (output as { text?: unknown }).text;
    if (typeof text === 'string' && text.trim()) return text;
  }
  return null;
}

/**
 * 连接显示名。查不到名字时**不编造**，退化成可追溯的 id 前缀。
 * 编一个「连接 1」出来看着整齐，但用户没法拿它回到连接列表 —— 那是假整齐。
 */
function agentLabel(step: HubStepResult): string {
  if (step.connection_name) return step.connection_name;
  if (step.connection_id) return `未命名连接（${step.connection_id.slice(0, 8)}…）`;
  return '未指定 Agent';
}

function usageLabel(meta: HubStepResult['meta']): string {
  const parts: string[] = [];
  const usage = meta?.usage;
  // 注意 usage 可能整个不存在 —— 失败步就没有。把它当唯一条件会连带把
  // 重试次数一起吞掉，而「打过几次」恰恰是失败步最该让人看到的信息。
  if (usage && typeof usage === 'object') {
    const u = usage as Record<string, unknown>;
    const total = u.total_tokens ?? u.completion_tokens;
    if (typeof total === 'number') parts.push(`${total} tokens`);
  }
  if (typeof meta?.attempts === 'number' && meta.attempts > 1) {
    parts.push(`重试 ${Number(meta.attempts) - 1} 次`);
  }
  return parts.join(' · ');
}

export function OrchestrationPanel({ connections }: { connections: HubConnection[] }) {
  useBase({ surface: 'web/src/components/hub/OrchestrationPanel' });

  const [preflight, setPreflight] = useState<HubOrchestratePreflight | null>(null);
  const [preflightError, setPreflightError] = useState('');

  const [mode, setMode] = useState<HubOrchestrateMode>('parallel');
  const [task, setTask] = useState('');
  const [bullets, setBullets] = useState('');
  const [stages, setStages] = useState<HubPipelineStage[]>([]);
  const [timeout, setTimeoutSeconds] = useState(120);
  const [summarize, setSummarize] = useState(false);

  const [running, setRunning] = useState(false);
  const [error, setError] = useState('');
  const [result, setResult] = useState<HubOrchestrationResult | null>(null);

  const refreshPreflight = useCallback(async () => {
    setPreflightError('');
    try {
      setPreflight(await hubApi.orchestrateCapabilities());
    } catch (e) {
      setPreflight(null);
      setPreflightError(errorMessage(e));
    }
  }, []);

  useEffect(() => {
    void refreshPreflight();
  }, [refreshPreflight]);

  // 连接集变化后，把已删除的连接从流水线里摘掉 ——
  // 留着会让提交直接撞后端 422，而错误信息远不如「这条连接已被删除」直观。
  useEffect(() => {
    const alive = new Set(connections.map((c) => c.id));
    setStages((prev) => {
      const kept = prev.filter((s) => alive.has(s.connection_id));
      return kept.length === prev.length ? prev : kept;
    });
  }, [connections]);

  const bulletList = useMemo(
    () => bullets.split('\n').map((s) => s.trim()).filter(Boolean),
    [bullets],
  );

  const canSubmit =
    !running &&
    (preflight?.can_orchestrate ?? false) &&
    task.trim() !== '' &&
    (mode === 'pipeline' ? stages.length > 0 && stages.every((s) => s.connection_id) : bulletList.length > 0);

  const onRun = () => {
    setRunning(true);
    setError('');
    setResult(null);
    const base = { timeout_seconds: timeout, summarize };
    const call =
      mode === 'pipeline'
        ? hubApi.orchestratePipeline({ ...base, stages } as HubPipelinePayload)
        : hubApi.orchestrate(
            {
              ...base,
              task: task.trim(),
              mode,
              ...(mode === 'parallel' ? { aspects: bulletList } : { steps: bulletList }),
            } as HubOrchestratePayload,
          );
    call
      .then((res) => setResult(res.orchestration))
      .catch((e) => setError(errorMessage(e)))
      .finally(() => setRunning(false));
  };

  const addStage = () => {
    setStages((prev) => [
      ...prev,
      { connection_id: connections[0]?.id ?? '', step: `第 ${prev.length + 1} 步`, instruction: '' },
    ]);
  };

  return (
    <section className="hub-orch" data-testid="hub-orch-panel">
      <div className="hub-orch-head">
        <h3>多Agent 编排</h3>
        {preflight && (
          <span className="muted" data-testid="hub-orch-preflight">
            可用 Agent {preflight.agent_count} · 能力 {preflight.capability_count} · 并发上限{' '}
            {preflight.max_workers}
          </span>
        )}
        {preflightError && (
          <span className="hub-health-detail" data-testid="hub-orch-preflight-error">
            前置体检失败：{preflightError}
          </span>
        )}
      </div>

      {preflight && !preflight.can_orchestrate && (
        <div className="notice danger" data-testid="hub-orch-no-agent">
          当前没有任何 Agent 连接，编排必然每步失败。请先新建连接并声明能力。
        </div>
      )}

      <div className="hub-filters" role="group" aria-label="编排形态">
        {(Object.keys(HUB_ORCHESTRATE_MODE_LABEL) as HubOrchestrateMode[]).map((m) => (
          <button
            key={m}
            type="button"
            aria-pressed={mode === m}
            className={mode === m ? 'hub-filter active' : 'hub-filter'}
            onClick={() => setMode(m)}
            data-testid={`hub-orch-mode-${m}`}
          >
            {HUB_ORCHESTRATE_MODE_LABEL[m]}
          </button>
        ))}
      </div>

      <p className="muted">
        {mode === 'parallel'
          ? '每个 Agent 拿同一任务的不同侧面，互不知情，适合多角度调研。'
          : mode === 'sequential'
            ? '前一步的输出拼进后一步的上下文，适合写作 / 审校类接力。'
            : '每步指名 Agent 并单独配 token 预算。注意：pipeline **不自动传递上一步的输出** —— 实测第2 步会回答「不知道你说的是哪个功能」。要么在指令里自带上下文，要么改用顺序接力。推理型模型也必须走这一种 —— 自动派单不透传 max_tokens，会只拿到思考过程。'}
      </p>

      <label className="hub-orch-label" htmlFor="hub-orch-task">
        任务
      </label>
      <textarea
        id="hub-orch-task"
        value={task}
        onChange={(e) => setTask(e.target.value)}
        rows={3}
        placeholder="如：为康源养老设计一套家属端通知方案"
        data-testid="hub-orch-task"
      />

      {mode === 'pipeline' ? (
        <>
          <label className="hub-orch-label" htmlFor="hub-orch-stage-instruction">
            流水线（每步指名 Agent）
          </label>
          {stages.length === 0 && (
            <p className="muted" data-testid="hub-orch-stages-empty">
              还没有步骤。点「添加步骤」逐个指名 Agent 与指令。
            </p>
          )}
          <ul className="hub-orch-stages">
            {stages.map((s, i) => (
              <li key={i} data-testid={`hub-orch-stage-${i}`}>
                <div className="hub-form-row">
                  <select
                    value={s.connection_id}
                    onChange={(e) =>
                      setStages((prev) =>
                        prev.map((x, j) => (j === i ? { ...x, connection_id: e.target.value } : x)),
                      )
                    }
                    data-testid={`hub-orch-stage-conn-${i}`}
                  >
                    <option value="">— 选 Agent —</option>
                    {connections.map((c) => (
                      <option key={c.id} value={c.id}>
                        {c.icon || '🔌'} {c.name}
                      </option>
                    ))}
                  </select>
                  <input
                    value={s.capability ?? ''}
                    onChange={(e) =>
                      setStages((prev) =>
                        prev.map((x, j) => (j === i ? { ...x, capability: e.target.value } : x)),
                      )
                    }
                    placeholder="能力名（可空，由路由/默认能力兜）"
                    data-testid={`hub-orch-stage-cap-${i}`}
                  />
                  <input
                    type="number"
                    min={256}
                    step={256}
                    value={String(s.params_extra?.max_tokens ?? '')}
                    onChange={(e) => {
                      const raw = e.target.value;
                      setStages((prev) =>
                        prev.map((x, j) => {
                          if (j !== i) return x;
                          const next = { ...(x.params_extra ?? {}) };
                          if (raw === '') delete next.max_tokens;
                          else next.max_tokens = Number(raw);
                          return { ...x, params_extra: next };
                        }),
                      );
                    }}
                    placeholder="max_tokens"
                    data-testid={`hub-orch-stage-budget-${i}`}
                  />
                  <button
                    type="button"
                    onClick={() => setStages((prev) => prev.filter((_, j) => j !== i))}
                    data-testid={`hub-orch-stage-drop-${i}`}
                  >
                    移除
                  </button>
                </div>
                <textarea
                  value={s.instruction ?? ''}
                  onChange={(e) =>
                    setStages((prev) =>
                      prev.map((x, j) => (j === i ? { ...x, instruction: e.target.value } : x)),
                    )
                  }
                  rows={2}
                  placeholder="这一步要这个 Agent 做什么"
                  data-testid={`hub-orch-stage-instruction-${i}`}
                />
              </li>
            ))}
          </ul>
          <button type="button" onClick={addStage} data-testid="hub-orch-stage-add">
            <LineIcon name="plus" size={16} />
            添加步骤
          </button>
        </>
      ) : (
        <>
          <label className="hub-orch-label" htmlFor="hub-orch-bullets">
            {mode === 'parallel' ? '分工侧面（每行一个）' : '步骤（每行一个）'}
          </label>
          <textarea
            id="hub-orch-bullets"
            value={bullets}
            onChange={(e) => setBullets(e.target.value)}
            rows={4}
            placeholder={mode === 'parallel' ? '技术可行性\n用户体验\n成本与商业模式' : '先拟大纲\n再逐段扩写\n最后审校措辞'}
            data-testid="hub-orch-bullets"
          />
        </>
      )}

      <div className="hub-form-row">
        <label className="hub-orch-inline">
          超时（秒）
          <input
            type="number"
            min={1}
            max={300}
            value={timeout}
            onChange={(e) => setTimeoutSeconds(Number(e.target.value) || 60)}
            data-testid="hub-orch-timeout"
          />
        </label>
        <label className="hub-orch-inline">
          <input
            type="checkbox"
            checked={summarize}
            onChange={(e) => setSummarize(e.target.checked)}
            data-testid="hub-orch-summarize"
          />
          末尾追加汇总步
        </label>
        <button
          type="button"
          className="ui-btn ui-btn--primary"
          onClick={onRun}
          disabled={!canSubmit}
          data-testid="hub-orch-run"
        >
          {running ? '编排中…' : '开始编排'}
        </button>
        <button type="button" onClick={() => void refreshPreflight()} data-testid="hub-orch-preflight-refresh">
          <LineIcon name="refresh" size={16} />
          重新体检
        </button>
      </div>

      {error && (
        <div className="notice danger" data-testid="hub-orch-error">
          编排请求失败：{error}
        </div>
      )}

      {result && (
        <div className="hub-orch-result" data-testid="hub-orch-result">
          <div className="hub-orch-summary">
            <strong data-tone={result.ok ? 'ok' : 'fail'} data-testid="hub-orch-verdict">
              {result.ok ? '全部步骤成功' : '未全部成功'}
            </strong>
            <span>
              {result.succeeded_count}/{result.step_count} 步成功
              {result.failed_count > 0 && ` · ${result.failed_count} 步失败`}
            </span>
            <span className="muted">耗时 {result.duration_ms}ms</span>
            {result.pending_interrupts && (
              <span className="hub-orch-warn" data-testid="hub-orch-interrupt">
                存在待确认中断，需人工介入后才能继续
              </span>
            )}
          </div>

          {result.error && (
            <div className="notice danger" data-testid="hub-orch-top-error">
              整体错误：{result.error}
            </div>
          )}

          <ol className="hub-orch-timeline">
            {result.steps.map((s) => {
              const degraded = typeof s.meta?.degraded_reason === 'string' ? s.meta.degraded_reason : '';
              const usage = usageLabel(s.meta);
              const text = outputText(s.output);
              return (
                <li key={s.index} data-testid={`hub-orch-step-${s.index}`} data-tone={s.ok ? 'ok' : 'fail'}>
                  <div className="hub-orch-step-head">
                    <span className="hub-orch-idx">#{s.index + 1}</span>
                    <strong>{s.step || `第 ${s.index + 1} 步`}</strong>
                    <span className="muted">{agentLabel(s)}</span>
                    {s.capability && <span className="muted">能力 {s.capability}</span>}
                    <span className={s.ok ? 'hub-orch-ok' : 'hub-orch-fail'}>
                      {s.ok ? '成功' : '失败'}
                    </span>
                    <span className="muted">{s.latency_ms}ms</span>
                    {usage && <span className="muted">{usage}</span>}
                  </div>

                  {degraded && (
                    <div className="hub-orch-degraded" data-testid={`hub-orch-degraded-${s.index}`}>
                      <strong>降级产物</strong>：{degraded}
                    </div>
                  )}

                  {s.ok ? (
                    <details>
                      <summary className="muted">展开输出</summary>
                      <pre>{text ?? JSON.stringify(s.output, null, 2)}</pre>
                    </details>
                  ) : (
                    <pre className="hub-orch-error-pre">{s.error || '（后端未给出原因）'}</pre>
                  )}
                </li>
              );
            })}
          </ol>

          {result.final_output !== null && result.final_output !== undefined && (
            <div data-testid="hub-orch-final">
              <h4>最终产出</h4>
              <pre>{outputText(result.final_output) ?? JSON.stringify(result.final_output, null, 2)}</pre>
            </div>
          )}
        </div>
      )}
    </section>
  );
}