/**
 * P1 · DSL 类型诊断面板（消费后端 ADR-02 收集式 IR 校验）。
 *
 * 铁律：后端返回多少条 Diagnostic，这里就**一次性全量**渲染多少条——
 * 绝不截断、绝不只显示第一条。这正是 IR 相对旧 fail-fast 校验器的核心价值
 * （旧校验器两个节点各有一个多余字段时永远只报第一个）。
 */
import type { DslDiagnostic } from '../../api/dslCanvas';

/** 某节点的全部诊断（画布红点悬停文本用）。 */
export function diagnosticsForNode(
  diagnostics: DslDiagnostic[], nodeId: string,
): DslDiagnostic[] {
  return diagnostics.filter((d) => d.node_id === nodeId);
}

/** 红点悬停文本：把该节点的每条诊断拼成「字段 错误码: 说明」。 */
export function nodeDiagnosticTitle(diagnostics: DslDiagnostic[]): string {
  return diagnostics
    .map((d) => `${d.field_path || '(文档)'} ${d.code}: ${d.message}`)
    .join('\n');
}

export function DslDiagnostics({ diagnostics }: { diagnostics: DslDiagnostic[] }) {
  if (!diagnostics.length) {
    return (
      <div className="fy-ir-diagnostics" data-testid="dsl-ir-diagnostics">
        <span className="badge accent" data-testid="dsl-ir-count">0</span>{' '}
        <span className="muted">类型校验通过（IR 0 条诊断）</span>
      </div>
    );
  }
  return (
    <div
      className="fy-ir-diagnostics fy-ir-invalid"
      data-testid="dsl-ir-diagnostics"
      role="alert"
    >
      <span className="badge danger" data-testid="dsl-ir-count">{diagnostics.length}</span>{' '}
      <strong>类型诊断（一次全量，共 {diagnostics.length} 条）</strong>
      <ul className="fy-ir-list">
        {diagnostics.map((d, i) => (
          <li
            key={`${d.node_id}-${d.field_path}-${d.code}-${i}`}
            className="fy-ir-item"
            data-testid="dsl-ir-item"
            title={`${d.code}: ${d.message}`}
          >
            <code>{d.code}</code> @ <code>{d.node_id || '(文档)'}</code>
            {d.field_path && <> · 字段 <code>{d.field_path}</code></>}
            <span className="fy-ir-message"> — {d.message}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}
