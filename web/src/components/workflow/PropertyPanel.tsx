/**
 * W5 工作流工坊 · 右侧属性面板（选中节点后编辑其参数）。
 *
 * 每个字段都带`<label>`，控件可键盘聚焦（UI_BASELINE：表单具备 label、
 * 足够点击区域、键盘焦点与状态文字）。
 *
 * 枚举取值与后端 `services/dsl_canvas.py` 的受限动词集严格一致：
 * ``MAP_OPS`` / ``FILTER_OPS`` / ``CONDITION_OPS`` / ``INPUT_KINDS`` /
 * ``OUTPUT_FORMATS``——写错一个字符后端就会422，所以前端只提供合法选项。
 */
import type { DslNodeType, DslTransformVerb } from '../../api/dslCanvas';
import type { EditorNode } from './FlowEditor';
import { paramsForVerb } from './FlowEditor';

const MAP_OPS = ['set', 'upper', 'lower'] as const;
const FILTER_OPS = ['eq', 'ne', 'gt', 'lt', 'contains'] as const;
const INPUT_KINDS = ['literal', 'text_lines'] as const;
const OUTPUT_FORMATS = ['json', 'text'] as const;
const VERBS: DslTransformVerb[] = ['map', 'filter', 'template'];

export interface PropertyPanelProps {
  node: EditorNode | null;
  onChange: (patch: Partial<EditorNode>) => void;
  onChangeParams: (patch: Record<string, unknown>) => void;
}

/** JSON 值编辑：编辑过程中允许暂时不合法（不阻塞输入），失焦时才尝试解析。 */
function JsonValueField({
  label, value, onCommit, testId,
}: {
  label: string;
  value: unknown;
  onCommit: (v: unknown) => void;
  testId?: string;
}) {
  return (
    <label className="fy-flow-field">
      {label}
      <input
        defaultValue={JSON.stringify(value ?? null)}
        onBlur={(e) => {
          try { onCommit(JSON.parse(e.target.value || 'null')); }
          catch { /* 保持原值：非法 JSON 不提交 */ }
        }}
        data-testid={testId}
      />
    </label>
  );
}

export function PropertyPanel({ node, onChange, onChangeParams }: PropertyPanelProps) {
  if (!node) {
    return (
      <div className="card" data-testid="flow-properties">
        <strong>属性</strong>
        <p className="muted">在画布里点选一个节点，这里会显示可编辑的参数。</p>
      </div>
    );
  }

  const p = node.params ?? {};

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
            <label className="fy-flow-field">
              数据类型
              <select
                value={String(p.kind ?? 'literal')}
                onChange={(e) => onChangeParams({ kind: e.target.value })}
                data-testid="prop-input-kind"
              >
                {INPUT_KINDS.map((k) => <option key={k} value={k}>{k}</option>)}
              </select>
            </label>
            {p.kind === 'text_lines' ? (
              <label className="fy-flow-field">
                文本（每行一条）
                <textarea
                  rows={3}
                  value={String(p.value ?? '')}
                  onChange={(e) => onChangeParams({ value: e.target.value })}
                  data-testid="prop-input-text"
                />
              </label>
            ) : (
              <JsonValueField
                label="JSON 值"
                value={p.value}
                onCommit={(v) => onChangeParams({ value: v })}
                testId="prop-input-value"
              />
            )}
          </>
        )}

        {node.type === 'transform' && (
          <>
            <label className="fy-flow-field">
              动词
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
            </label>

            {node.verb === 'template' && (
              <label className="fy-flow-field">
                模板（{'{field}'} 插值）
                <input
                  value={String(p.template ?? '')}
                  onChange={(e) => onChangeParams({ template: e.target.value })}
                  data-testid="prop-template"
                />
              </label>
            )}

            {node.verb === 'map' && (
              <>
                <label className="fy-flow-field">
                  操作
                  <select
                    value={String(p.op ?? 'set')}
                    onChange={(e) => onChangeParams({ op: e.target.value })}
                    data-testid="prop-map-op"
                  >
                    {MAP_OPS.map((o) => <option key={o} value={o}>{o}</option>)}
                  </select>
                </label>
                {p.op === 'set' && (
                  <>
                    <label className="fy-flow-field">
                      目标字段
                      <input
                        value={String(p.field ?? '')}
                        onChange={(e) => onChangeParams({ field: e.target.value })}
                        data-testid="prop-map-field"
                      />
                    </label>
                    <label className="fy-flow-field">
                      新值（支持 {'{field}'} 插值）
                      <input
                        value={String(p.value ?? '')}
                        onChange={(e) => onChangeParams({ value: e.target.value })}
                        data-testid="prop-map-value"
                      />
                    </label>
                  </>
                )}
              </>
            )}

            {node.verb === 'filter' && (
              <>
                <label className="fy-flow-field">
                  字段
                  <input
                    value={String(p.field ?? '')}
                    onChange={(e) => onChangeParams({ field: e.target.value })}
                    data-testid="prop-filter-field"
                  />
                </label>
                <label className="fy-flow-field">
                  比较
                  <select
                    value={String(p.op ?? 'eq')}
                    onChange={(e) => onChangeParams({ op: e.target.value })}
                    data-testid="prop-filter-op"
                  >
                    {FILTER_OPS.map((o) => <option key={o} value={o}>{o}</option>)}
                  </select>
                </label>
                <JsonValueField
                  label="阈值（JSON）"
                  value={p.value}
                  onCommit={(v) => onChangeParams({ value: v })}
                  testId="prop-filter-value"
                />
              </>
            )}
          </>
        )}

        {node.type === 'output' && (
          <label className="fy-flow-field">
            输出格式
            <select
              value={String(p.format ?? 'text')}
              onChange={(e) => onChangeParams({ format: e.target.value })}
              data-testid="prop-output-format"
            >
              {OUTPUT_FORMATS.map((f) => <option key={f} value={f}>{f}</option>)}
            </select>
          </label>
        )}
      </div>
    </div>
  );
}