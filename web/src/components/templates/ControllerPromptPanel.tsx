/**
 * 总控提示词面板（P13 · A-开箱模板-02②③④）。
 *
 * 出厂提示词**默认可见、可编辑**；改坏了给出**明确警告**并说明后果，但
 * **不阻断保存**——技术用户有权自行决定（这是需求原文的口径）。
 * 「恢复出厂总控提示词」一键回滚。
 */
import { LineIcon } from '../ui/LineIcon';
import type { ControllerSpec } from '../../api/templates';

export function ControllerPromptPanel({
  controller,
  draft,
  warnings,
  touched,
  busy = false,
  onDraftChange,
  onCheck,
  onRestore,
}: {
  controller: ControllerSpec;
  draft: string;
  warnings: string[];
  /** 用户是否动过草稿（没动过就不显示「未保存的修改」提示）。 */
  touched: boolean;
  busy?: boolean;
  onDraftChange: (value: string) => void;
  onCheck: () => void;
  onRestore: () => void;
}) {
  const broken = warnings.length > 0;
  return (
    <section className="fy-tpl-controller" aria-label="总控 Agent 出厂预设">
      <header className="row" style={{ justifyContent: 'space-between', alignItems: 'baseline' }}>
        <h3>总控 Agent 出厂预设</h3>
        <button type="button" className="small ghost" onClick={onRestore} disabled={busy}>
          <LineIcon name="rotate" size={14} /> 恢复出厂总控提示词
        </button>
      </header>

      <p className="muted">
        总控只调度、不执行：任务分配 / 路由划分 / 调度跟进 / 信息同步 / 状态更新。
        禁行规则：
        {controller.forbidden_rules.map((r) => (
          <span key={r} className="badge">
            {r}
          </span>
        ))}
      </p>

      <label className="fy-tpl-prompt-label">
        <span className="muted">系统提示词（可编辑、可另存为新模板）</span>
        <textarea
          rows={12}
          aria-label="总控系统提示词"
          value={draft}
          onChange={(e) => onDraftChange(e.target.value)}
          onBlur={onCheck}
        />
      </label>

      {touched && (
        <p className="fy-tpl-dirty" role="status">
          有未保存的修改 —— 覆盖会随「创建系统」一起提交，或点「恢复出厂总控提示词」放弃。
        </p>
      )}

      {broken ? (
        <div className="fy-tpl-warning" role="alert">
          <LineIcon name="alert" size={16} />
          <div>
            <strong>警告：提示词可能已破坏总控的职责边界</strong>
            <ul>
              {warnings.map((w) => (
                <li key={w}>{w}</li>
              ))}
            </ul>
            <p className="muted">
              不会阻断保存——你有权这么改；但总控一旦自己动手，分配、路由与收口就没人管了。
            </p>
          </div>
        </div>
      ) : (
        <p className="fy-tpl-ok" role="status">
          <LineIcon name="check" size={16} /> 边界检查通过：总控只调度、不执行。
        </p>
      )}
    </section>
  );
}
