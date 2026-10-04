// 新建 / 编辑连接表单。
// 关键点：credential_fields 里 secret=true 的字段进 credentials（后端加密），
// 其余进 config（明文可见）。表单本身不回显已有凭证——只能重填。
import { useState } from 'react';
import type { HubConnection, HubCredentialField, HubKind } from '../../api/hub';
import { HUB_KIND_LABEL } from '../../api/hub';

const KINDS: HubKind[] = [
  'openai_chat',
  'anthropic',
  'mcp_server',
  'http_webhook',
  'tool_plugin',
  'knowledge_source',
];

export interface ConnectionFormSeed {
  name?: string;
  kind?: HubKind;
  icon?: string;
  description?: string;
  config?: Record<string, unknown>;
  credential_fields?: HubCredentialField[];
  secret_fields?: string[];
  capabilities?: { name: string; description?: string; tags?: string[] }[];
}

export interface ConnectionFormSubmit {
  name: string;
  kind: HubKind;
  icon: string;
  description: string;
  config: Record<string, unknown>;
  credentials: Record<string, unknown>;
  secret_fields: string[];
  credential_fields: HubCredentialField[];
  capabilities: { name: string; description?: string; tags?: string[] }[];
}

const DEFAULT_FIELDS: HubCredentialField[] = [
  { key: 'api_key', label: 'API Key', secret: true, required: true },
];

export function ConnectionForm({
  seed,
  editing,
  busy,
  notice,
  onSubmit,
  onCancel,
}: {
  seed: ConnectionFormSeed | null;
  editing: HubConnection | null;
  busy: boolean;
  notice: string;
  onSubmit: (payload: ConnectionFormSubmit) => void;
  onCancel: () => void;
}) {
  const [name, setName] = useState(seed?.name ?? '');
  const [kind, setKind] = useState<HubKind>(seed?.kind ?? 'openai_chat');
  const [icon, setIcon] = useState(seed?.icon ?? '🔌');
  const [description, setDescription] = useState(seed?.description ?? '');
  const [fields, setFields] = useState<HubCredentialField[]>(
    seed?.credential_fields?.length ? seed.credential_fields : DEFAULT_FIELDS,
  );
  // 明文 config 的可编辑键值对（base_url / model / command ...）
  const [configRows, setConfigRows] = useState<{ key: string; value: string }[]>(() =>
    Object.entries(seed?.config ?? {}).map(([key, value]) => ({ key, value: String(value) })),
  );
  const [credValues, setCredValues] = useState<Record<string, string>>({});
  const [capsText, setCapsText] = useState(
    (seed?.capabilities ?? []).map((c) => c.name).join('、'),
  );

  const submit = (e: React.FormEvent) => {
    e.preventDefault();
    const config: Record<string, unknown> = {};
    for (const row of configRows) {
      const k = row.key.trim();
      if (k) config[k] = row.value.trim();
    }
    const credentials: Record<string, unknown> = {};
    const secretFields: string[] = [];
    for (const f of fields) {
      const v = (credValues[f.key] ?? '').trim();
      if (f.secret) {
        secretFields.push(f.key);
        if (v) credentials[f.key] = v;
      } else if (v) {
        config[f.key] = v;
      }
    }
    onSubmit({
      name: name.trim(),
      kind,
      icon: icon.trim() || '🔌',
      description: description.trim(),
      config,
      credentials,
      secret_fields: secretFields,
      credential_fields: fields,
      capabilities: capsText
        .split(/[、,，\s]+/)
        .map((s) => s.trim())
        .filter(Boolean)
        .map((n) => ({ name: n })),
    });
  };

  return (
    <form className="hub-form" onSubmit={submit} data-testid="hub-connection-form">
      <h3>{editing ? `编辑连接：${editing.name}` : '新建连接'}</h3>
      {editing && (
        <p className="muted">
          已保存的凭证以掩码存储，页面读不到明文。如需更换请直接填新值，留空表示保持原值。
        </p>
      )}

      <div className="hub-form-row">
        <label>
          名称
          <input value={name} onChange={(e) => setName(e.target.value)} required data-testid="hub-form-name" />
        </label>
        <label>
          图标
          <input value={icon} onChange={(e) => setIcon(e.target.value)} />
        </label>
        <label>
          类型
          <select value={kind} onChange={(e) => setKind(e.target.value as HubKind)} data-testid="hub-form-kind">
            {KINDS.map((k) => (
              <option key={k} value={k}>
                {HUB_KIND_LABEL[k]}
              </option>
            ))}
          </select>
        </label>
      </div>

      <label>
        说明
        <input value={description} onChange={(e) => setDescription(e.target.value)} />
      </label>

      <fieldset>
        <legend>连接参数（明文可见）</legend>
        {configRows.map((row, i) => (
          <div className="hub-form-row" key={`cfg-${i}`}>
            <input
              value={row.key}
              placeholder="键，如 base_url"
              onChange={(e) =>
                setConfigRows((rows) => rows.map((r, j) => (j === i ? { ...r, key: e.target.value } : r)))
              }
            />
            <input
              value={row.value}
              placeholder="值"
              onChange={(e) =>
                setConfigRows((rows) => rows.map((r, j) => (j === i ? { ...r, value: e.target.value } : r)))
              }
            />
            <button type="button" onClick={() => setConfigRows((rows) => rows.filter((_, j) => j !== i))}>
              移除
            </button>
          </div>
        ))}
        <button type="button" onClick={() => setConfigRows((rows) => [...rows, { key: '', value: '' }])}>
          添加参数
        </button>
      </fieldset>

      <fieldset>
        <legend>凭证（加密存储，永不回显）</legend>
        {fields.map((f, i) => (
          <div className="hub-form-row" key={`cred-${f.key}-${i}`}>
            <input
              value={f.key}
              placeholder="字段名，如 api_key"
              onChange={(e) =>
                setFields((fs) => fs.map((x, j) => (j === i ? { ...x, key: e.target.value } : x)))
              }
            />
            <input
              value={f.label}
              placeholder="显示名"
              onChange={(e) =>
                setFields((fs) => fs.map((x, j) => (j === i ? { ...x, label: e.target.value } : x)))
              }
            />
            <label className="hub-inline">
              <input
                type="checkbox"
                checked={f.secret}
                onChange={(e) =>
                  setFields((fs) => fs.map((x, j) => (j === i ? { ...x, secret: e.target.checked } : x)))
                }
              />
              加密
            </label>
            <input
              type="password"
              autoComplete="new-password"
              placeholder="填写新值"
              value={credValues[f.key] ?? ''}
              onChange={(e) => setCredValues((v) => ({ ...v, [f.key]: e.target.value }))}
            />
            <button type="button" onClick={() => setFields((fs) => fs.filter((_, j) => j !== i))}>
              移除
            </button>
          </div>
        ))}
        <button
          type="button"
          onClick={() =>
            setFields((fs) => [...fs, { key: '', label: '', secret: true, required: false }])
          }
        >
          添加凭证字段
        </button>
      </fieldset>

      <label>
        能力标签（顿号或逗号分隔）
        <input
          value={capsText}
          onChange={(e) => setCapsText(e.target.value)}
          placeholder="如：chat、web_search"
          data-testid="hub-form-caps"
        />
      </label>

      {notice && <div className="notice warn" data-testid="hub-form-notice">{notice}</div>}

      <div className="hub-form-actions">
        <button type="submit" disabled={busy} data-testid="hub-form-submit">
          {editing ? '保存修改' : '创建连接'}
        </button>
        <button type="button" onClick={onCancel} disabled={busy}>
          取消
        </button>
      </div>
    </form>
  );
}
