/**
 * W5 工作流工坊 ·「一句话生成」面板（任务的小白层）。
 *
 * 诚实契约（总纲铁律 3 / FROZEN_CONTRACT §11）——本组件**绝不伪造成功**：
 *  - 后端 503 `model_not_configured` → 显示「未配置模型」并给出配置指引，
 *    绝不用预置模板冒充「已生成」；
 *  - 后端 422 `workflow_generation_failed` → 原样展示校验错误（含重试次数）；
 *  - 生成成功 → 把真实返回的 DSL 交给上层载入画布，并显示实际服务的
 *    provider:model；发生降级时（``degraded_from`` 非空）**明确标注**，
 *    不让用户以为用的是他要求的那个模型。
 */
import { useCallback, useEffect, useState } from 'react';
import { ApiError } from '../../api/client';
import { workflowGenApi, type DslDocument } from '../../api/workflowGen';
import { useBase } from '../../hooks/useAutosave';

export interface GeneratePanelProps {
  /** 生成成功后把真实 DSL 交给父组件载入画布。 */
  onGenerated: (doc: DslDocument, sourcePrompt: string) => void;
}

const EXAMPLES = [
  '每天读文件并发邮件',
  '把输入里的空行去掉再统计行数',
  '筛出年龄大于 30 的记录并输出成文本',
];

export function GeneratePanel({ onGenerated }: GeneratePanelProps) {
  useBase({ surface: 'web/src/components/workflow/GeneratePanel' });
  const [prompt, setPrompt] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<{ message: string; hint: string } | null>(null);
  const [meta, setMeta] = useState('');
  const [configured, setConfigured] = useState<boolean | null>(null);

  // 先问后端「到底配没配模型」，如实显示，别让用户点了才撞 503。
  useEffect(() => {
    let alive = true;
    void workflowGenApi.status()
      .then((s) => { if (alive) setConfigured(s.model_configured); })
      .catch(() => { if (alive) setConfigured(null); });
    return () => { alive = false; };
  }, []);

  const generate = useCallback(async () => {
    const requirement = prompt.trim();
    if (!requirement) {
      setError({ message: '请先描述你的工作流需求', hint: '' });
      return;
    }
    setBusy(true);
    setError(null);
    setMeta('');
    try {
      const res = await workflowGenApi.generate(requirement);
      onGenerated(res.dsl, requirement);
      const provider = res.provider_id ? `${res.provider_id}:${res.model}` : res.model;
      const degraded = res.degraded_from
       ? `｜注意：实际由 ${provider} 应答（原请求 ${res.degraded_from}，原因：${res.degraded_reason}）`
        : '';
      setMeta(
        `已生成：${res.dsl.nodes.length} 节点 / ${res.dsl.edges.length} 边｜` +
        `模型 ${provider}｜尝试 ${res.attempts.length} 次｜费用 ${res.settled_usd} USD${degraded}`,
      );
      setConfigured(true);
    } catch (e) {
      if (e instanceof ApiError && e.status === 503) {
        setConfigured(false);
        setError({
          message: e.body?.message ?? '未配置模型',
          hint: '请设置 FY_MODEL_API_KEY 与 FY_MODEL_BASE_URL（OpenAI 兼容端点，如 DeepSeek 或本地 Ollama）后重启服务。',
        });
      } else if (e instanceof ApiError && e.status === 422) {
        const att = e.body?.message ?? '模型未能给出合法 DSL';
        setError({
          message: att,
          hint: '这是模型输出的真实校验错误（已自动重试一次）。可换更明确的描述，或直接在下方画布手工搭建。',
        });
      } else {
        setError({
          message: e instanceof Error ? e.message : String(e),
          hint: '',
        });
      }
    } finally {
      setBusy(false);
    }
  }, [prompt, onGenerated]);

  return (
    <div className="fy-flow-generate card" data-testid="flow-generate">
      <div className="fy-flow-generate-head">
        <strong>一句话生成工作流</strong>
        <span
          className={`fy-flow-badge${configured === false ? ' is-warn' : ''}`}
          data-testid="flow-model-state"
        >
          {configured === null ? '模型状态未知' : configured ? '模型已就绪' : '未配置模型'}
        </span>
      </div>
      <p className="muted">
        用自然语言描述需求，模型只会输出**受限动词集**内的 DSL，并由服务端再校验一次
        （模型说合法不算合法）。生成结果可以直接拖拽编辑。
      </p>

      <label className="fy-flow-field">
        你的需求
        <textarea
          rows={2}
          value={prompt}
          placeholder="例如：每天读文件并发邮件"
          onChange={(e) => setPrompt(e.target.value)}
          data-testid="flow-generate-prompt"
        />
      </label>

      <div className="fy-flow-generate-actions">
        <button
          type="button"
          className="btn btn-sm primary"
          onClick={() => void generate()}
          disabled={busy}
          data-testid="flow-generate-run"
        >{busy ? '生成中…' : '生成'}</button>
        {EXAMPLES.map((ex) => (
          <button
            key={ex}
            type="button"
            className="btn btn-sm"
            onClick={() => setPrompt(ex)}
            data-testid={`flow-example-${ex}`}
          >{ex}</button>
        ))}
      </div>

      {error && (
        <div className="fy-flow-error" role="alert" data-testid="flow-generate-error">
          <strong>生成失败</strong>
          <span>{error.message}</span>
          {error.hint && <span className="fy-flow-error-hint">{error.hint}</span>}
        </div>
      )}
      {meta && <div className="fy-flow-notice" data-testid="flow-generate-meta">{meta}</div>}
    </div>
  );
}