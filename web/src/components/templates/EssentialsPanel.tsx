/**
 * 八类隐性必备条件面板（P13 · A-开箱模板-03②③）。
 *
 * - 每一项都展示**出厂默认值**与**简短说明**（为什么是这个值、改了会怎样）；
 * - 支持**逐项覆盖**，覆盖后仍可一键恢复出厂值；
 * - 覆盖传的是「值」，不是「整条必备项」，所以恢复出厂永远能回到干净状态。
 */
import { useState } from 'react';
import type { EssentialItem } from '../../api/templates';
import { LineIcon } from '../ui/LineIcon';
import { useBase } from '../../hooks/useAutosave';

function toEditorText(value: unknown): string {
  if (typeof value === 'string') return value;
  return JSON.stringify(value ?? null, null, 2);
}

/** 覆盖值：能解析成 JSON 就用解析结果（对象/数组/数字），否则按纯文本。 */
function fromEditorText(text: string): unknown {
  const trimmed = text.trim();
  if (trimmed === '') return '';
  if (trimmed === 'null') return null;
  try {
    return JSON.parse(trimmed);
  } catch {
    return text;
  }
}

function renderValue(value: unknown) {
  if (value === null || value === undefined) return <span className="muted">—</span>;
  if (typeof value === 'object') {
    return (
      <ul className="fy-tpl-essential-obj">
        {Object.entries(value as Record<string, unknown>).map(([k, v]) => (
          <li key={k}>
            <code>{k}</code>
            <span>: {typeof v === 'object' ? JSON.stringify(v) : String(v)}</span>
          </li>
        ))}
      </ul>
    );
  }
  return <span>{String(value)}</span>;
}

export function EssentialsPanel({
  items,
  onOverride,
  onRestoreFactory,
  busy = false,
}: {
  items: EssentialItem[];
  onOverride: (key: string, value: unknown) => void;
  onRestoreFactory: () => void;
  busy?: boolean;
}) {
  useBase({ surface: 'web/src/components/templates/EssentialsPanel' });
  const [editing, setEditing] = useState<string | null>(null);
  const [draft, setDraft] = useState('');

  return (
    <section className="fy-tpl-essentials" aria-label="隐性必备条件（出厂已填好）">
      <header className="row" style={{ justifyContent: 'space-between', alignItems: 'baseline' }}>
        <h3>隐性必备条件 · 出厂已填好</h3>
        <button type="button" className="small ghost" onClick={onRestoreFactory} disabled={busy}>
          <LineIcon name="rotate" size={14} /> 恢复出厂值
        </button>
      </header>
      <p className="muted">
        这八项小白通常不知道该填什么。出厂全部有默认值，可直接用；想改就改，改完还能一键还原。
      </p>

      <ul className="fy-tpl-essential-list">
        {items.map((item) => {
          const overridden = JSON.stringify(item.value) !== JSON.stringify(item.factory_value);
          return (
            <li key={item.key} className={overridden ? 'overridden' : ''}>
              <div className="fy-tpl-essential-head">
                <strong>{item.label}</strong>
                <code className="muted">{item.key}</code>
                {overridden && <span className="badge">已覆盖</span>}
              </div>
              <div className="fy-tpl-essential-value">{renderValue(item.value)}</div>
              <details>
                <summary className="muted">为什么是这个值 / 改了会怎样</summary>
                <p>{item.explain}</p>
              </details>

              {editing === item.key ? (
                <div className="fy-tpl-essential-edit">
                  <label>
                    <span className="muted">覆盖为（对象/数组请用 JSON）</span>
                    <textarea
                      rows={5}
                      value={draft}
                      aria-label={`覆盖 ${item.label}`}
                      onChange={(e) => setDraft(e.target.value)}
                    />
                  </label>
                  <div className="row" style={{ gap: '0.4rem' }}>
                    <button
                      type="button"
                      className="small"
                      onClick={() => {
                        onOverride(item.key, fromEditorText(draft));
                        setEditing(null);
                      }}
                    >
                      应用覆盖
                    </button>
                    <button
                      type="button"
                      className="small ghost"
                      onClick={() => {
                        onOverride(item.key, item.factory_value);
                        setEditing(null);
                      }}
                    >
                      恢复此项出厂值
                    </button>
                    <button type="button" className="small ghost" onClick={() => setEditing(null)}>
                      取消
                    </button>
                  </div>
                </div>
              ) : (
                <button
                  type="button"
                  className="small ghost"
                  disabled={!item.overridable}
                  onClick={() => {
                    setEditing(item.key);
                    setDraft(toEditorText(item.value));
                  }}
                >
                  <LineIcon name="edit" size={14} /> 覆盖此项
                </button>
              )}
            </li>
          );
        })}
      </ul>
    </section>
  );
}
