// W6 超级中台适配器中心 —— 统一管理面。
//
// 诚实约定（总纲铁律 3 / FROZEN_CONTRACT §11）：
//  - 探活失败显示后端给的真实 detail，不美化不省略；
//  - 调用失败（200 + ok:false）单独展示 error 与 output，不吞掉；
//  - 「未探活」是独立状态，不与「可用」合并；
//  - implemented=false 的预置显示「内置骨架 · 未接入」；
//  - 凭证永远没有明文入口，页面只可能看到掩码。
import { useCallback, useEffect, useState } from 'react';
import { hubApi, HUB_KIND_LABEL } from '../api/hub';
import type {
  HubCapability,
  HubCapabilityRow,
  HubConnection,
  HubInvokeResult,
  HubPreset,
  HubRouteCandidate,
} from '../api/hub';
import { errorMessage } from '../components/ui';
import { ConnectionCard } from '../components/hub/ConnectionCard';
import { ConnectionForm, type ConnectionFormSeed, type ConnectionFormSubmit } from '../components/hub/ConnectionForm';
import { PresetGallery } from '../components/hub/PresetGallery';
import { ManifestImport } from '../components/hub/ManifestImport';
import { CapabilityTable, RouteLab } from '../components/hub/CapabilityPanel';

type Filter = 'all' | 'ai' | 'tool' | 'mcp' | 'data' | 'other';

const FILTERS: { key: Filter; label: string }[] = [
  { key: 'all', label: '全部' },
  { key: 'ai', label: '模型' },
  { key: 'tool', label: '工具' },
  { key: 'mcp', label: 'MCP' },
  { key: 'data', label: '数据' },
  { key: 'other', label: '其他' },
];

function groupOf(conn: HubConnection): string {
  if (conn.group === 'mcp') return 'mcp';
  if (conn.group === 'ai') return 'ai';
  if (conn.group === 'data') return 'data';
  if (conn.group === 'tool') return 'tool';
  return 'other';
}

export function HubPage() {
  const [connections, setConnections] = useState<HubConnection[]>([]);
  const [presets, setPresets] = useState<HubPreset[]>([]);
  const [caps, setCaps] = useState<HubCapabilityRow[]>([]);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [listError, setListError] = useState('');
  const [notice, setNotice] = useState('');
  const [invokeResult, setInvokeResult] = useState<{ conn: string; result: HubInvokeResult } | null>(null);
  const [filter, setFilter] = useState<Filter>('all');

  const [formOpen, setFormOpen] = useState(false);
  const [seed, setSeed] = useState<ConnectionFormSeed | null>(null);
  const [editing, setEditing] = useState<HubConnection | null>(null);
  const [formNotice, setFormNotice] = useState('');

  const [manifestError, setManifestError] = useState('');
  const [manifestNotice, setManifestNotice] = useState('');

  const [hint, setHint] = useState('');
  const [routeBusy, setRouteBusy] = useState(false);
  const [candidates, setCandidates] = useState<HubRouteCandidate[]>([]);
  const [routeSearched, setRouteSearched] = useState(false);
  const [routeError, setRouteError] = useState('');

  const refresh = useCallback(async () => {
    setLoading(true);
    setListError('');
    try {
      const [c, p, k] = await Promise.all([
        hubApi.listConnections(),
        hubApi.listPresets(),
        hubApi.listCapabilities(),
      ]);
      setConnections(c.connections);
      setPresets(p.presets);
      setCaps(k.capabilities);
    } catch (e) {
      setListError(errorMessage(e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const withBusy = async (fn: () => Promise<void>) => {
    setBusy(true);
    try {
      await fn();
    } finally {
      setBusy(false);
      await refresh();
    }
  };

  const onUsePreset = (p: HubPreset) => {
    setSeed({
      name: p.name,
      kind: p.kind,
      icon: p.icon,
      description: p.description,
      config: p.config,
      credential_fields: p.credential_fields.length > 0 ? p.credential_fields : undefined,
      capabilities: p.capabilities.length > 0 ? p.capabilities : [{ name: p.capability_tags[0] ?? p.id }],
    });
    setEditing(null);
    setFormNotice(p.implemented ? p.note : `${p.name} 是内置骨架，尚未真正接入；创建后探活会如实报不可用。`);
    setFormOpen(true);
  };

  const onSubmitForm = (payload: ConnectionFormSubmit) => {
    void withBusy(async () => {
      try {
        if (editing) {
          await hubApi.updateConnection(editing.id, {
            name: payload.name,
            icon: payload.icon,
            description: payload.description,
            config: payload.config,
            credentials: payload.credentials,
            capabilities: payload.capabilities,
          });
          setNotice(`已保存「${payload.name}」。`);
        } else {
          await hubApi.createConnection({
            name: payload.name,
            kind: payload.kind,
            icon: payload.icon,
            description: payload.description,
            config: payload.config,
            credentials: payload.credentials,
            secret_fields: payload.secret_fields,
            credential_fields: payload.credential_fields,
            capabilities: payload.capabilities,
          });
          setNotice(`已创建「${payload.name}」。建议先点「探活」确认真实可用。`);
        }
        setFormOpen(false);
        setSeed(null);
        setEditing(null);
      } catch (e) {
        setFormNotice(`提交失败：${errorMessage(e)}`);
      }
    });
  };

  const onHealth = (conn: HubConnection) => {
    void withBusy(async () => {
      setNotice('');
      try {
        const res = await hubApi.healthCheck(conn.id);
        setNotice(
          res.report.ok
            ? `「${conn.name}」探活通过（${res.report.latency_ms}ms）。`
            : `「${conn.name}」探活失败：${res.report.detail || '后端未给出原因'}`,
        );
      } catch (e) {
        setNotice(`探活请求失败：${errorMessage(e)}`);
      }
    });
  };

  const onHealthAll = () => {
    void withBusy(async () => {
      try {
        const res = await hubApi.healthCheckAll();
        const bad = res.results
          .filter((r) => !r.report.ok)
          .map((r) => `${r.connection_id}：${r.report.detail || '未知原因'}`);
        setNotice(
          `批量探活：${res.healthy}/${res.count} 可用。` + (bad.length ? ` 失败项 —— ${bad.join('；')}` : ''),
        );
      } catch (e) {
        setNotice(`批量探活失败：${errorMessage(e)}`);
      }
    });
  };

  const onInvoke = (conn: HubConnection) => {
    void withBusy(async () => {
      setNotice('');
      setInvokeResult(null);
      try {
        const res = await hubApi.invoke(conn.id, { action: 'invoke', params: {} });
        setInvokeResult({ conn: conn.name, result: res.result });
        setNotice(
          res.result.ok
            ? `「${conn.name}」调用成功（${res.result.latency_ms}ms）。`
            : `「${conn.name}」调用未成功：${res.result.error || '适配器未给出原因'}`,
        );
      } catch (e) {
        setNotice(`调用请求失败：${errorMessage(e)}`);
      }
    });
  };

  const onDelete = (conn: HubConnection) => {
    void withBusy(async () => {
      try {
        await hubApi.deleteConnection(conn.id);
        setNotice(`已删除「${conn.name}」及其能力声明。`);
      } catch (e) {
        setNotice(`删除失败：${errorMessage(e)}`);
      }
    });
  };

  const onToggleState = (conn: HubConnection) => {
    void withBusy(async () => {
      const next = conn.state === 'disabled' ? 'active' : 'disabled';
      try {
        await hubApi.updateConnection(conn.id, { state: next });
        setNotice(`「${conn.name}」已${next === 'disabled' ? '停用' : '启用'}。`);
      } catch (e) {
        setNotice(`状态切换失败：${errorMessage(e)}`);
      }
    });
  };

  const onDropCapability = (row: HubCapabilityRow) => {
    void withBusy(async () => {
      try {
        await hubApi.unregisterCapability(row.connection_id, row.capability.name);
        setNotice(`已移除能力「${row.capability.name}」。`);
      } catch (e) {
        setNotice(`移除失败：${errorMessage(e)}`);
      }
    });
  };

  const onImportManifest = (text: string) => {
    void withBusy(async () => {
      setManifestError('');
      setManifestNotice('');
      try {
        const res = await hubApi.importManifest({ text, filename: 'pasted.yaml' });
        const warns = Array.isArray((res.connection as { warnings?: string[] }).warnings)
          ? ((res.connection as unknown as { warnings: string[] }).warnings ?? [])
          : [];
        setManifestNotice(
          `已导入「${res.connection.name}」（状态：${res.connection.state}）。` +
            (warns.length ? ` 警告：${warns.join('；')}` : '') +
            ' 探活通过后才算真的可用。',
        );
      } catch (e) {
        setManifestError(`导入失败：${errorMessage(e)}`);
      }
    });
  };

  const onRunRoute = () => {
    setRouteBusy(true);
    setRouteError('');
    setRouteSearched(true);
    setCandidates([]);
    hubApi
      .route({ hint, top_k: 5 })
      .then((res) => setCandidates(res.candidates))
      .catch((e) => setRouteError(errorMessage(e)))
      .finally(() => setRouteBusy(false));
  };

  const shown = connections.filter((c) => (filter === 'all' ? true : groupOf(c) === filter));
  const usedKinds = new Set(connections.map((c) => c.kind));

  return (
    <>
      <div className="page-head">
        <h2>🧲 超级中台</h2>
        <span className="muted">
          统一适配层 · 六类对象 · 凭证加密存储 · 能力路由（确定性规则，非模型决策）
        </span>
      </div>

      <div className="hub-toolbar">
        <button
          type="button"
          onClick={() => {
            setSeed(null);
            setEditing(null);
            setFormNotice('');
            setFormOpen(true);
          }}
          data-testid="hub-new"
        >
          新建连接
        </button>
        <button type="button" onClick={() => void onHealthAll()} disabled={busy || connections.length === 0} data-testid="hub-health-all">
          批量探活
        </button>
        <ManifestImport
          busy={busy}
          notice={manifestNotice}
          error={manifestError}
          onImport={onImportManifest}
        />
      </div>

      {notice && <div className="notice" data-testid="hub-notice">{notice}</div>}
      {listError && <div className="notice danger" data-testid="hub-list-error">{listError}</div>}

      {formOpen && (
        <ConnectionForm
          key={editing?.id ?? (seed?.name ?? 'new')}
          seed={seed}
          editing={editing}
          busy={busy}
          notice={formNotice}
          onSubmit={onSubmitForm}
          onCancel={() => {
            setFormOpen(false);
            setSeed(null);
            setEditing(null);
          }}
        />
      )}

      <section className="hub-connections">
        <div className="hub-filters" role="tablist" aria-label="连接分组">
          {FILTERS.map((f) => (
            <button
              key={f.key}
              type="button"
              role="tab"
              aria-selected={filter === f.key}
              className={filter === f.key ? 'hub-filter active' : 'hub-filter'}
              onClick={() => setFilter(f.key)}
              data-testid={`hub-filter-${f.key}`}
            >
              {f.label}
            </button>
          ))}
        </div>

        {loading && <div className="muted">读取中…</div>}
        {!loading && !listError && connections.length === 0 && (
          <div className="muted" data-testid="hub-empty">
            还没有任何连接。可以从下面的预置库一键创建，或粘贴 manifest 导入。
          </div>
        )}
        {!loading && connections.length > 0 && shown.length === 0 && (
          <div className="muted" data-testid="hub-filter-empty">
            该分组下暂无连接。
          </div>
        )}

        <div className="hub-grid">
          {shown.map((c) => (
            <ConnectionCard
              key={c.id}
              conn={c}
              busy={busy}
              onHealth={onHealth}
              onInvoke={onInvoke}
              onDelete={onDelete}
              onToggleState={onToggleState}
            />
          ))}
        </div>
      </section>

      {invokeResult && (
        <section className="hub-invoke-result" data-testid="hub-invoke-result">
          <h3>调用结果：{invokeResult.conn}</h3>
          <p>
            状态：
            <strong data-tone={invokeResult.result.ok ? 'ok' : 'fail'}>
              {invokeResult.result.ok ? '成功' : '失败'}
            </strong>
            {invokeResult.result.error && <span> · 原因：{invokeResult.result.error}</span>}
          </p>
          <pre>{JSON.stringify(invokeResult.result.output, null, 2)}</pre>
        </section>
      )}

      <PresetGallery presets={presets} busy={busy} onUse={onUsePreset} />

      <section className="hub-capabilities">
        <h3>能力清单（{caps.length}）</h3>
        <CapabilityTable rows={caps} busy={busy} onDrop={onDropCapability} />
      </section>

      <RouteLab
        hint={hint}
        onHint={setHint}
        onRun={onRunRoute}
        busy={routeBusy}
        candidates={candidates}
        searched={routeSearched}
        error={routeError}
      />

      <p className="muted hub-footnote">
        当前已接入类型：
        {usedKinds.size === 0
          ? '无'
          : Array.from(usedKinds).map((k) => HUB_KIND_LABEL[k] ?? k).join('、')}
        。凭证以 Fernet 加密落库，接口只回掩码，页面无明文入口。
      </p>
    </>
  );
}

export type { HubCapability };
