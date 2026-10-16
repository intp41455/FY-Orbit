// 市场远程源导入入口（I-12 · 补前端闭环）。
//
// 诚实性约束（AGENTS 铁律 1/2）：
//  - 没有远程源连接就如实说「还没有登记远程源」，不给假按钮；
//  - 导入失败把后端错误码原样带出来（code + message），绝不吞掉、绝不假装成功；
//  - 导入只是「运输」：结果里 imported / skipped 逐条如实展示，跳过原因直出后端文案。
import { useState } from 'react';
import { hubApi, type HubConnection } from '../../api/hub';
import { pluginsApi } from '../../api/plugins';
import { errorMessage } from '../ui';
import { useBase } from '../../hooks/useAutosave';
import { LineIcon } from '../ui/LineIcon';

/** 错误码必须原样露出：门禁/克隆失败要靠 code 定位，前端无权改写后端判定。 */
function describeError(e: unknown): string {
  const code =
    (e as { body?: { code?: string } }).body?.code ?? (e as { code?: string }).code;
  const message = errorMessage(e);
  return code ? `${code}：${message}` : message;
}

export function RemoteSourceImport({ onImported }: { onImported: () => void }) {
  useBase({ surface: 'web/src/components/plugins/RemoteSourceImport' });
  const [open, setOpen] = useState(false);
  const [sources, setSources] = useState<HubConnection[]>([]);
  const [selected, setSelected] = useState('');
  const [loading, setLoading] = useState(false);
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState('');
  const [failed, setFailed] = useState(false);

  const openPanel = async () => {
    setOpen(true);
    setLoading(true);
    setNote('');
    setFailed(false);
    try {
      const res = await hubApi.listConnections({ kind: 'market_source' });
      const rows = res.connections ?? [];
      setSources(rows);
      setSelected(rows[0]?.id ?? '');
    } catch (e) {
      setNote(`读取远程源列表失败：${describeError(e)}`);
    } finally {
      setLoading(false);
    }
  };

  const runImport = async () => {
    if (!selected) return;
    setBusy(true);
    setFailed(false);
    try {
      const res = await pluginsApi.importSource(selected);
      const imported = res.imported.length;
      const skipped = res.skipped ?? [];
      // 逐条如实列出：跳过原因来自后端（含错误码），不能压成「部分成功」。
      const detail = skipped.map((s) => `${s.id}：${s.reason}`).join('；');
      setNote(
        `导入完成：${imported} 个包已进入待上架` +
          (skipped.length ? `；${skipped.length} 个已跳过（${detail}）` : '') +
          '。未过门禁的包在下方陈列，当前不可安装。',
      );
      onImported();
    } catch (e) {
      setFailed(true);
      setNote(`导入失败：${describeError(e)}`);
    } finally {
      setBusy(false);
    }
  };

  if (!open) {
    return (
      <button type="button" className="ui-btn" onClick={() => void openPanel()} data-testid="market-source-open">
        <LineIcon name="download" size={16} />
        从远程源导入
      </button>
    );
  }

  return (
    <section
      className="fy-market-source"
      data-testid="market-source-panel"
      style={{ display: 'flex', flexWrap: 'wrap', gap: 'var(--ui-s-2)', alignItems: 'center', margin: 'var(--ui-s-2) 0' }}
    >
      {loading && <span className="muted">读取远程源中…</span>}
      {!loading && sources.length === 0 && (
        <span className="muted" data-testid="market-source-empty">
          还没有登记任何远程源（请先在「超级中台」里新建 kind=market_source 的连接：HTTP 索引 / Git 仓库 / 本地目录）。
        </span>
      )}
      {sources.length > 0 && (
        <>
          <label htmlFor="market-source-select" className="muted" style={{ fontSize: 'var(--ui-fs-sm)' }}>
            远程源：
          </label>
          <select
            id="market-source-select"
            className="ui-input"
            value={selected}
            onChange={(e) => setSelected(e.target.value)}
            data-testid="market-source-select"
            aria-label="选择市场远程源"
          >
            {sources.map((c) => (
              <option key={c.id} value={c.id}>
                {c.name}（{String(c.config?.source_type ?? 'http')}）
              </option>
            ))}
          </select>
          <button
            type="button"
            className="ui-btn ui-btn--primary"
            disabled={busy || selected === ''}
            onClick={() => void runImport()}
            data-testid="market-source-import"
          >
            {busy ? '导入中…' : '导入这个源的包'}
          </button>
        </>
      )}
      {note && (
        <span
          role="status"
          className={failed ? 'ui-toast ui-toast--error' : 'ui-toast'}
          data-testid="market-source-note"
        >
          {note}
        </span>
      )}
      <button type="button" className="ui-btn" onClick={() => setOpen(false)} disabled={busy} data-testid="market-source-close">
        收起
      </button>
    </section>
  );
}