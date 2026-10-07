// B1 · G2：设置页「ima 知识库」凭证卡。
//
// 字段 App ID / API Key / Secret Key / Base URL → `knowledgeApi.configureSource('ima')`
// （后端走 hub Fernet 加密存储，不回显明文）；状态读 `knowledgeApi.imaStatus()`。
// 诚实性：只展示「哪些字段已填」（布尔），保存结果按后端回的 storage/persist_restart 提示。
import { useCallback, useEffect, useState } from 'react';
import { knowledgeApi } from '../../api/knowledge';
import type { ImaChannelStatus } from '../../api/knowledge';
import { errorMessage } from '../ui';

const FIELDS: { key: string; label: string; secret: boolean }[] = [
  { key: 'app_id', label: 'App ID', secret: false },
  { key: 'api_key', label: 'API Key', secret: true },
  { key: 'secret_key', label: 'Secret Key', secret: true },
  { key: 'base_url', label: 'Base URL（REST 兜底，可选）', secret: false },
];

export function ImaKnowledgeCard() {
  const [status, setStatus] = useState<ImaChannelStatus | null>(null);
  const [values, setValues] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      setStatus(await knowledgeApi.imaStatus());
    } catch (e) {
      setError(errorMessage(e));
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const save = async () => {
    const payload: Record<string, string> = {};
    for (const [k, v] of Object.entries(values)) {
      if (v.trim()) payload[k] = v.trim();
    }
    if (Object.keys(payload).length === 0) {
      setError('请至少填写一个字段');
      setNotice(null);
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const r = await knowledgeApi.configureSource('ima', payload);
      setNotice(
        r.persist_restart
          ? 'ima 凭证已保存（加密存储，重启后仍有效）'
          : 'ima 凭证已保存（仅内存，重启后失效）',
      );
      setValues({});
      await refresh();
    } catch (e) {
      setError(errorMessage(e));
      setNotice(null);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="card st-section" data-testid="ima-knowledge-card">
      <h3>ima 知识库</h3>
      <p className="muted">
        接入陛下的 ima 公共知识库（库 {status?.kb_id ?? '7509748362520236'}）。凭证加密存储，不回显明文。
      </p>
      {status && (
        <p className="muted" data-testid="ima-card-status">
          {status.configured ? '通道已配置' : '未接入'}
          {status.detail ? ` · ${status.detail}` : ''}
        </p>
      )}
      {FIELDS.map((f) => (
        <label key={f.key} className="row" style={{ gap: '0.5rem', marginTop: '0.35rem' }}>
          <span style={{ minWidth: '9rem' }}>{f.label}</span>
          <input
            type={f.secret ? 'password' : 'text'}
            aria-label={`ima-${f.key}`}
            autoComplete="off"
            value={values[f.key] ?? ''}
            placeholder={status?.credentials_present?.[f.key] ? '已填写（留空则不改动）' : ''}
            onChange={(e) => setValues((v) => ({ ...v, [f.key]: e.target.value }))}
          />
        </label>
      ))}
      <div className="row" style={{ gap: '0.5rem', marginTop: '0.6rem' }}>
        <button type="button" className="small" disabled={busy} onClick={() => void save()}>
          保存 ima 凭证
        </button>
      </div>
      {notice && (
        <p className="muted" role="status" data-testid="ima-card-notice">
          {notice}
        </p>
      )}
      {error && (
        <p className="notice danger" role="alert" data-testid="ima-card-error">
          {error}
        </p>
      )}
    </div>
  );
}
