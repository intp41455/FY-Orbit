// 预置库面板：点「使用预置」把默认值+凭证字段回填到新建表单。
// implemented=false 的预置必须显式标注「内置骨架 · 未接入」，不做成看起来可用。
import type { HubPreset } from '../../api/hub';
import { kindLabel } from './hubFormat';

export function PresetGallery({
  presets,
  busy,
  onUse,
}: {
  presets: HubPreset[];
  busy: boolean;
  onUse: (preset: HubPreset) => void;
}) {
  if (presets.length === 0) return null;
  return (
    <section className="hub-presets" data-testid="hub-preset-gallery">
      <h3>预置库（{presets.length}）</h3>
      <p className="muted">预置只提供默认参数与凭证字段声明，不含任何真实凭证；点击后仍需你填写 Key。</p>
      <div className="hub-preset-grid">
        {presets.map((p) => (
          <div className="hub-preset" key={p.id} data-testid={`hub-preset-${p.id}`}>
            <div className="hub-preset-head">
              <span aria-hidden="true">{p.icon || '🧩'}</span>
              <strong>{p.name}</strong>
              {!p.implemented && (
                <span className="hub-preset-skeleton" data-testid={`hub-preset-skeleton-${p.id}`}>
                  内置骨架 · 未接入
                </span>
              )}
            </div>
            <p className="muted">{kindLabel(p.kind)}</p>
            <p>{p.description}</p>
            {p.note && <p className="hub-preset-note">{p.note}</p>}
            {p.credential_fields.length > 0 && (
              <p className="muted">
                需要：
                {p.credential_fields
                  .map((f) => `${f.label || f.key}${f.required ? '（必填）' : ''}`)
                  .join('、')}
              </p>
            )}
            <button type="button" onClick={() => onUse(p)} disabled={busy}>
              使用预置
            </button>
          </div>
        ))}
      </div>
    </section>
  );
}
