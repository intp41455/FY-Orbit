// W3 适配器管理卡：展示适配器真实状态（未接入 / 已配置未验证 / 可用），
// 并提供凭证填写、测试连接、同步三个动作。未实现的适配器不提供同步按钮。
//
// W6 验收 F2：source_id 形如 `hub:<conn_id>` 的卡片来自超级中台的
// knowledge_source 连接。这类源的凭证在**中台连接**上配置，不在这里填，
// 因此不渲染凭证输入框与「清除凭证」——避免两条写路径让用户 unsure
// 哪份才是真的。只保留「测试连接」与「同步」（这两个动作对 hub 源有效）。
import { useState } from 'react';
import { isHubSource, type KBSourceStatus } from '../../api/knowledge';
import { credentialHint, sourceStateLabel } from './kbFormat';
import { useBase } from '../../hooks/useAutosave';

export interface SourceCardProps {
  source: KBSourceStatus;
  onConfigure: (sourceId: string, values: Record<string, string>) => Promise<void>;
  onForget: (sourceId: string) => Promise<void>;
  onProbe: (sourceId: string) => Promise<void>;
  onSync: (sourceId: string) => Promise<void>;
  busy?: boolean;
}

export function SourceCard({
  source,
  onConfigure,
  onForget,
  onProbe,
  onSync,
  busy = false,
}: SourceCardProps) {
  useBase({ surface: 'web/src/components/knowledge/SourceCard' });
  const [values, setValues] = useState<Record<string, string>>({});
  const state = sourceStateLabel(source);
  const implemented = source.source_id !== 'baidu_pan';
  const fromHub = isHubSource(source);

  return (
    <div className="card kb-source-card" data-testid={`kb-source-${source.source_id}`}>
      <div className="row" style={{ justifyContent: 'space-between' }}>
        <strong>{source.display_name}</strong>
        <span className={`badge ${state.tone === 'ok' ? 'ok' : state.tone}`}>{state.label}</span>
      </div>
      {fromHub && (
        <p className="muted" data-testid={`kb-source-origin-${source.source_id}`}>
          来源：超级中台连接
          {source.connection_state_text ? ` · ${source.connection_state_text}` : ''}
          {source.health && source.health.ok === false && source.health.detail
            ? ` · 中台最近探活失败：${source.health.detail}`
            : ''}
        </p>
      )}
      <p className="muted" data-testid={`kb-source-detail-${source.source_id}`}>
        {source.detail}
      </p>
      <div className="muted">{credentialHint(source)}</div>
      <div className="muted">
        能力：服务端检索 {source.capabilities.searchable ? '支持' : '不支持'} · 全文{' '}
        {source.capabilities.full_text ? '支持' : '不支持'} · 增量{' '}
        {source.capabilities.incremental ? '支持' : '不支持'} · 自动重试{' '}
        {source.capabilities.retryable ? '支持' : '不支持'}
      </div>

      {implemented && (
        <>
          {!fromHub &&
            source.credential_fields.map((field) => (
              <label key={field} className="row" style={{ gap: '0.5rem', marginTop: '0.35rem' }}>
                <span style={{ minWidth: '5.5rem' }}>{field}</span>
                <input
                  type={field === 'api_key' || field === 'app_secret' || field === 'secret_key' ? 'password' : 'text'}
                  aria-label={`${source.source_id}-${field}`}
                  value={values[field] ?? ''}
                  placeholder={source.credentials_present[field] ? '已填写（留空则不改动）' : ''}
                  onChange={(e) => setValues((v) => ({ ...v, [field]: e.target.value }))}
                />
              </label>
            ))}
          <div className="row" style={{ gap: '0.5rem', marginTop: '0.6rem', flexWrap: 'wrap' }}>
            {!fromHub && (
              <button
                type="button"
                className="small"
                disabled={busy}
                onClick={() => void onConfigure(source.source_id, values)}
              >
                保存凭证
              </button>
            )}
            <button
              type="button"
              className="small ghost"
              disabled={busy}
              onClick={() => void onProbe(source.source_id)}
            >
              测试连接
            </button>
            <button
              type="button"
              className="small ghost"
              disabled={busy || !source.configured}
              onClick={() => void onSync(source.source_id)}
            >
              同步到本地索引
            </button>
            {!fromHub && (
              <button
                type="button"
                className="small ghost"
                disabled={busy || !source.configured}
                onClick={() => void onForget(source.source_id)}
              >
                清除凭证
              </button>
            )}
            {fromHub && (
              <span className="muted" data-testid={`kb-source-hint-${source.source_id}`}>
                凭证在「超级中台」页该连接上配置（加密存储）
              </span>
            )}
          </div>
        </>
      )}
    </div>
  );
}