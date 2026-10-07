/**
 * W5 工作流工坊 · 右侧属性面板（选中节点后编辑其参数）。
 *
 * 每个字段都带`<label>`，控件可键盘聚焦（UI_BASELINE：表单具备 label、
 * 足够点击区域、键盘焦点与状态文字）。
 *
 * 枚举取值与后端 `services/dsl_canvas.py` 的受限动词集严格一致：
 * ``MAP_OPS`` / ``FILTER_OPS`` / ``CONDITION_OPS`` / ``INPUT_KINDS`` /
 * ``OUTPUT_FORMATS``——写错一个字符后端就会422，所以前端只提供合法选项。
 *
 * P1（ADR-02）：可选传入后端收集式 IR 校验的 `DslDiagnostic[]`；
 * 命中本节点某 `field_path` 的诊断会让该字段显示**红框 + 内联错误文案**
 * （`fy-field-invalid` / `role="alert"`），多字段同时出错时每个字段各自标错。
 */
import type { DslDiagnostic, DslNodeType, DslTransformVerb } from '../../api/dslCanvas';
import type { EditorNode } from './FlowEditor';
import { paramsForVerb } from './FlowEditor';
import { useBase } from '../../hooks/useAutosave';

const MAP_OPS = ['set', 'upper', 'lower'] as const;
const FILTER_OPS = ['eq', 'ne', 'gt', 'lt', 'contains'] as const;
const INPUT_KINDS = ['literal', 'text_lines'] as const;
const OUTPUT_FORMATS = ['json', 'text'] as const;
const AGGREGATE_OPS = ['count', 'sum', 'min', 'max', 'avg', 'first', 'last', 'join', 'unique'] as const;
const MERGE_OPS = ['concat', 'first', 'last'] as const;
/**
 * 受限动词白名单（与后端 `services/dsl_canvas.py` 的 `VERB_REGISTRY`逐字对齐）。
 * 顺序即注册表顺序，前端不做增删——多一个后端就 422，少一个则画布表达力缺失。
 */
const VERBS: DslTransformVerb[] = [
  'map', 'filter', 'template',
  'branch', 'aggregate', 'merge',
  'agent', 'confirm', 'artifact',
];

export interface PropertyPanelProps {
  node: EditorNode | null;
  onChange: (patch: Partial<EditorNode>) => void;
  onChangeParams: (patch: Record<string, unknown>) => void;
  /** P1 · 该画布全部 IR 诊断（可选；按 node_id + field_path 匹配到字段）。 */
  diagnostics?: DslDiagnostic[];
}

/** 取本节点某字段路径上的第一条诊断；无则 undefined。 */
export function diagForField(
  diagnostics: DslDiagnostic[] | undefined,
  nodeId: string,
  fieldPath: string,
): DslDiagnostic | undefined {
  return diagnostics?.find((d) => d.node_id === nodeId && d.field_path === fieldPath);
}

interface ParamFieldProps {
  label: string;
  /** IR 诊断的字段路径（如 `params.op` / `verb`）。 */
  path: string;
  nodeId: string;
  diagnostics?: DslDiagnostic[];
  children: React.ReactNode;
}

/**
 * 字段壳：label + 控件 + 该字段的内联错误文案。
 * 有诊断时整字段加 `fy-field-invalid`（红框由 CSS 提供）。
 */
function ParamField({ label, path, nodeId, diagnostics, children }: ParamFieldProps) {
  const diag = diagForField(diagnostics, nodeId, path);
  return (
    <label
      className={`fy-flow-field${diag ? ' fy-field-invalid' : ''}`}
      data-field-path={path}
    >
      {label}
      {children}
      {diag && (
        <span className="fy-field-error-text" role="alert" data-testid={`field-error-${path}`}>
          {diag.code}: {diag.message}
        </span>
      )}
    </label>
  );
}

/** JSON 值编辑：编辑过程中允许暂时不合法（不阻塞输入），失焦时才尝试解析。 */
function JsonValueField({
  label, value, onCommit, testId, path, nodeId, diagnostics,
}: {
  label: string;
  value: unknown;
  onCommit: (v: unknown) => void;
  testId?: string;
  path: string;
  nodeId: string;
  diagnostics?: DslDiagnostic[];
}) {
  const diag = diagForField(diagnostics, nodeId, path);
  return (
    <label
      className={`fy-flow-field${diag ? ' fy-field-invalid' : ''}`}
      data-field-path={path}
    >
      {label}
      <input
        defaultValue={JSON.stringify(value ?? null)}
        onBlur={(e) => {
          try { onCommit(JSON.parse(e.target.value || 'null')); }
          catch { /* 保持原值：非法 JSON 不提交 */ }
        }}
        data-testid={testId}
      />
      {diag && (
        <span className="fy-field-error-text" role="alert" data-testid={`field-error-${path}`}>
          {diag.code}: {diag.message}
        </span>
      )}
    </label>
  );
}

export function PropertyPanel({ node, onChange, onChangeParams, diagnostics }: PropertyPanelProps) {
  useBase({ surface: 'web/src/components/workflow/PropertyPanel' });
  if (!node) {
    return (
      <div className="card" data-testid="flow-properties">
        <strong>属性</strong>
        <p className="muted">在画布里点选一个节点，这里会显示可编辑的参数。</p>
      </div>
    );
  }

  const p = node.params ?? {};
  const f = (path: string) => diagForField(diagnostics, node.id, path);
  void f;

  return (
    <div className="card" data-testid="flow-properties">
      <strong>属性 · {node.id}</strong>
      <p className="muted fy-flow-prop-type">
        类型 <code>{node.type as DslNodeType}</code>
        {node.type === 'transform' && <> ·动词 <code>{node.verb ?? 'template'}</code></>}
      </p>

      <div className="fy-flow-fields">
        {node.type === 'input' && (
          <>
            <ParamField label="数据类型" path="params.kind" nodeId={node.id} diagnostics={diagnostics}>
              <select
                value={String(p.kind ?? 'literal')}
                onChange={(e) => onChangeParams({ kind: e.target.value })}
                data-testid="prop-input-kind"
              >
                {INPUT_KINDS.map((k) => <option key={k} value={k}>{k}</option>)}
              </select>
            </ParamField>
            {p.kind === 'text_lines' ? (
              <ParamField label="文本（每行一条）" path="params.value" nodeId={node.id} diagnostics={diagnostics}>
                <textarea
                  rows={3}
                  value={String(p.value ?? '')}
                  onChange={(e) => onChangeParams({ value: e.target.value })}
                  data-testid="prop-input-text"
                />
              </ParamField>
            ) : (
              <JsonValueField
                label="JSON 值"
                value={p.value}
                onCommit={(v) => onChangeParams({ value: v })}
                testId="prop-input-value"
                path="params.value"
                nodeId={node.id}
                diagnostics={diagnostics}
              />
            )}
          </>
        )}

        {node.type === 'transform' && (
          <>
            <ParamField label="动词" path="verb" nodeId={node.id} diagnostics={diagnostics}>
              <select
                value={node.verb ?? 'template'}
                onChange={(e) => {
                  const verb = e.target.value as DslTransformVerb;
                  // 换动词时整组替换参数，避免上一个动词的残留字段让后端 422。
                  onChange({ verb, params: paramsForVerb(verb) });
                }}
                data-testid="prop-verb"
              >
                {VERBS.map((v) => <option key={v} value={v}>{v}</option>)}
              </select>
            </ParamField>

            {node.verb === 'template' && (
              <ParamField label="模板（{'{field}'} 插值）" path="params.template" nodeId={node.id} diagnostics={diagnostics}>
                <input
                  value={String(p.template ?? '')}
                  onChange={(e) => onChangeParams({ template: e.target.value })}
                  data-testid="prop-template"
                />
              </ParamField>
            )}

            {node.verb === 'map' && (
              <>
                <ParamField label="操作" path="params.op" nodeId={node.id} diagnostics={diagnostics}>
                  <select
                    value={String(p.op ?? 'set')}
                    onChange={(e) => onChangeParams({ op: e.target.value })}
                    data-testid="prop-map-op"
                  >
                    {MAP_OPS.map((o) => <option key={o} value={o}>{o}</option>)}
                  </select>
                </ParamField>
                {p.op === 'set' && (
                  <>
                    <ParamField label="目标字段" path="params.field" nodeId={node.id} diagnostics={diagnostics}>
                      <input
                        value={String(p.field ?? '')}
                        onChange={(e) => onChangeParams({ field: e.target.value })}
                        data-testid="prop-map-field"
                      />
                    </ParamField>
                    <ParamField label="新值（支持 {'{field}'} 插值）" path="params.value" nodeId={node.id} diagnostics={diagnostics}>
                      <input
                        value={String(p.value ?? '')}
                        onChange={(e) => onChangeParams({ value: e.target.value })}
                        data-testid="prop-map-value"
                      />
                    </ParamField>
                  </>
                )}
              </>
            )}

            {node.verb === 'filter' && (
              <>
                <ParamField label="字段" path="params.field" nodeId={node.id} diagnostics={diagnostics}>
                  <input
                    value={String(p.field ?? '')}
                    onChange={(e) => onChangeParams({ field: e.target.value })}
                    data-testid="prop-filter-field"
                  />
                </ParamField>
                <ParamField label="比较" path="params.op" nodeId={node.id} diagnostics={diagnostics}>
                  <select
                    value={String(p.op ?? 'eq')}
                    onChange={(e) => onChangeParams({ op: e.target.value })}
                    data-testid="prop-filter-op"
                  >
                    {FILTER_OPS.map((o) => <option key={o} value={o}>{o}</option>)}
                  </select>
                </ParamField>
                <JsonValueField
                  label="阈值（JSON）"
                  value={p.value}
                  onCommit={(v) => onChangeParams({ value: v })}
                  testId="prop-filter-value"
                  path="params.value"
                  nodeId={node.id}
                  diagnostics={diagnostics}
                />
              </>
            )}

            {node.verb === 'branch' && (
              <>
                <ParamField label="字段" path="params.field" nodeId={node.id} diagnostics={diagnostics}>
                  <input
                    value={String(p.field ?? '')}
                    onChange={(e) => onChangeParams({ field: e.target.value })}
                    data-testid="prop-branch-field"
                  />
                </ParamField>
                <ParamField label="比较" path="params.op" nodeId={node.id} diagnostics={diagnostics}>
                  <select
                    value={String(p.op ?? 'eq')}
                    onChange={(e) => onChangeParams({ op: e.target.value })}
                    data-testid="prop-branch-op"
                  >
                    {FILTER_OPS.map((o) => <option key={o} value={o}>{o}</option>)}
                  </select>
                </ParamField>
                <JsonValueField
                  label="阈值（JSON）"
                  value={p.value}
                  onCommit={(v) => onChangeParams({ value: v })}
                  testId="prop-branch-value"
                  path="params.value"
                  nodeId={node.id}
                  diagnostics={diagnostics}
                />
                <ParamField label="命中标签" path="params.then_label" nodeId={node.id} diagnostics={diagnostics}>
                  <input
                    value={String(p.then_label ?? '')}
                    onChange={(e) => onChangeParams({ then_label: e.target.value })}
                    data-testid="prop-branch-then"
                  />
                </ParamField>
                <ParamField label="未命中标签" path="params.else_label" nodeId={node.id} diagnostics={diagnostics}>
                  <input
                    value={String(p.else_label ?? '')}
                    onChange={(e) => onChangeParams({ else_label: e.target.value })}
                    data-testid="prop-branch-else"
                  />
                </ParamField>
              </>
            )}

            {node.verb === 'aggregate' && (
              <>
                <ParamField label="聚合算子" path="params.op" nodeId={node.id} diagnostics={diagnostics}>
                  <select
                    value={String(p.op ?? 'count')}
                    onChange={(e) => onChangeParams({ op: e.target.value })}
                    data-testid="prop-aggregate-op"
                  >
                    {AGGREGATE_OPS.map((o) => <option key={o} value={o}>{o}</option>)}
                  </select>
                </ParamField>
                <ParamField label="字段（sum/min/max/avg 必填）" path="params.field" nodeId={node.id} diagnostics={diagnostics}>
                  <input
                    value={String(p.field ?? '')}
                    onChange={(e) => onChangeParams({ field: e.target.value })}
                    data-testid="prop-aggregate-field"
                  />
                </ParamField>
                <ParamField label="连接符（join 用）" path="params.sep" nodeId={node.id} diagnostics={diagnostics}>
                  <input
                    value={String(p.sep ?? ',')}
                    onChange={(e) => onChangeParams({ sep: e.target.value })}
                    data-testid="prop-aggregate-sep"
                  />
                </ParamField>
              </>
            )}

            {node.verb === 'merge' && (
              <ParamField label="汇聚策略" path="params.mode" nodeId={node.id} diagnostics={diagnostics}>
                <select
                  value={String(p.mode ?? 'concat')}
                  onChange={(e) => onChangeParams({ mode: e.target.value })}
                  data-testid="prop-merge-mode"
                >
                  {MERGE_OPS.map((m) => <option key={m} value={m}>{m}</option>)}
                </select>
              </ParamField>
            )}

            {node.verb === 'agent' && (
              <ParamField label="Agent 名" path="params.agent" nodeId={node.id} diagnostics={diagnostics}>
                <input
                  value={String(p.agent ?? '')}
                  onChange={(e) => onChangeParams({ agent: e.target.value })}
                  data-testid="prop-agent-name"
                />
              </ParamField>
            )}

            {node.verb === 'confirm' && (
              <>
                <p className="muted">
                  人工确认动词位：HITL 中断/恢复尚未接入，执行时该节点必定失败。
                </p>
                <ParamField label="确认话术" path="params.prompt" nodeId={node.id} diagnostics={diagnostics}>
                  <input
                    value={String(p.prompt ?? '')}
                    onChange={(e) => onChangeParams({ prompt: e.target.value })}
                    data-testid="prop-confirm-prompt"
                  />
                </ParamField>
                <ParamField label="角色" path="params.role" nodeId={node.id} diagnostics={diagnostics}>
                  <input
                    value={String(p.role ?? '')}
                    onChange={(e) => onChangeParams({ role: e.target.value })}
                    data-testid="prop-confirm-role"
                  />
                </ParamField>
              </>
            )}

            {node.verb === 'artifact' && (
              <>
                <ParamField label="产物名" path="params.name" nodeId={node.id} diagnostics={diagnostics}>
                  <input
                    value={String(p.name ?? '')}
                    onChange={(e) => onChangeParams({ name: e.target.value })}
                    data-testid="prop-artifact-name"
                  />
                </ParamField>
                <ParamField label="产物类型" path="params.kind" nodeId={node.id} diagnostics={diagnostics}>
                  <input
                    value={String(p.kind ?? 'generic')}
                    onChange={(e) => onChangeParams({ kind: e.target.value })}
                    data-testid="prop-artifact-kind"
                  />
                </ParamField>
              </>
            )}
          </>
        )}

        {node.type === 'output' && (
          <ParamField label="输出格式" path="params.format" nodeId={node.id} diagnostics={diagnostics}>
            <select
              value={String(p.format ?? 'text')}
              onChange={(e) => onChangeParams({ format: e.target.value })}
              data-testid="prop-output-format"
            >
              {OUTPUT_FORMATS.map((fm) => <option key={fm} value={fm}>{fm}</option>)}
            </select>
          </ParamField>
        )}
      </div>
    </div>
  );
}
