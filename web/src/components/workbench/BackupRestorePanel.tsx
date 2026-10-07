// P1-14 备份回滚 UI (BackupRestorePanel).
//
// 备份：把当前文件内容一键暂存到 /api/stash —— title 自动带文件名+时间戳，
//       metadata 记录文件路径 + 内容 sha256。
// 回滚：从暂存记录列表选择一条 → 确认弹窗 → 走现有保存链路
//       (workbenchApi.writeFile，带 expected_revision 乐观锁) 写回文件。
//       写回后重新读取文件内容并计算 sha256，与备份 metadata 中的哈希比对：
//       一致显示绿标，面板同时展示两个比对值。
// 列表：分页（客户端翻页）/ 单条删除 / 清空，全部复用 stash API。
import { useCallback, useEffect, useMemo, useState } from 'react';
import { workbenchApi } from '../../api/workbench';
import { metaString, stashApi, type StashRecord } from '../../api/stash';
import { errorMessage } from '../ui';
import { sha256Hex } from './sha256';
import { useBase } from '../../hooks/useAutosave';

const PAGE_SIZE = 8;

interface HashVerify {
  match: boolean;
  backupHash: string | null;
  currentHash: string;
  source: 'metadata' | 'record-content' | 'none';
}

interface Props {
  workspaceId: string | null;
  path: string | null;
  /** 回滚写盘成功后回调（工作台用其刷新编辑器缓冲区）。 */
  onRolledBack?: () => void;
}

function fileStamp(): string {
  const d = new Date();
  const p = (n: number) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`;
}

export function BackupRestorePanel({ workspaceId, path, onRolledBack }: Props) {
  useBase({ surface: 'web/src/components/workbench/BackupRestorePanel' });
  const [records, setRecords] = useState<StashRecord[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [page, setPage] = useState(0);
  const [onlyCurrent, setOnlyCurrent] = useState(true);
  const [expandedId, setExpandedId] = useState<string | null>(null);

  const [backingUp, setBackingUp] = useState(false);
  const [lastBackup, setLastBackup] = useState<{ id: string; title: string; sha256: string } | null>(null);

  const [confirmTarget, setConfirmTarget] = useState<StashRecord | null>(null);
  const [rollingBack, setRollingBack] = useState(false);
  const [verify, setVerify] = useState<HashVerify | null>(null);

  const [clearArmed, setClearArmed] = useState(false);

  const reload = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const r = await stashApi.list(200);
      setRecords(r.records);
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void reload();
  }, [reload]);

  const visible = useMemo(() => {
    const filtered = onlyCurrent && path ? records.filter((r) => metaString(r, 'path') === path) : records;
    return filtered;
  }, [records, onlyCurrent, path]);

  const pageCount = Math.max(1, Math.ceil(visible.length / PAGE_SIZE));
  const safePage = Math.min(page, pageCount - 1);
  const pageRows = visible.slice(safePage * PAGE_SIZE, safePage * PAGE_SIZE + PAGE_SIZE);

  // --------------------------------------------------------------- 备份 ----
  async function backupCurrentFile() {
    if (!workspaceId || !path) return;
    setBackingUp(true);
    setError(null);
    setNotice(null);
    setVerify(null);
    try {
      const fc = await workbenchApi.readFile(workspaceId, path);
      if (fc.binary) {
        setError('二进制文件不支持文本备份。');
        return;
      }
      const hash = sha256Hex(fc.content);
      const name = path.split('/').pop() ?? path;
      const res = await stashApi.stage({
        content: fc.content,
        title: `${name} · ${fileStamp()}`,
        content_type: 'text/plain',
        metadata: {
          path,
          workspace_id: workspaceId,
          sha256: hash,
          size_bytes: fc.size_bytes,
          revision: fc.revision,
        },
      });
      setLastBackup({ id: res.record.id, title: res.record.title, sha256: hash });
      setNotice(`已备份：${res.record.title}`);
      await reload();
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setBackingUp(false);
    }
  }

  // --------------------------------------------------------------- 回滚 ----
  async function doRollback() {
    const rec = confirmTarget;
    if (!rec || !workspaceId) return;
    const targetPath = metaString(rec, 'path') ?? path;
    if (!targetPath) {
      setError('该记录未记录文件路径，且当前未选择文件，无法确定回滚目标。');
      setConfirmTarget(null);
      return;
    }
    setRollingBack(true);
    setError(null);
    setNotice(null);
    setVerify(null);
    try {
      // 1) 取当前版本号（乐观锁），2) 走与编辑器保存一致的写回链路，
      // 3) 重新读取并比对哈希。
      const fresh = await workbenchApi.readFile(workspaceId, targetPath);
      await workbenchApi.writeFile(workspaceId, targetPath, {
        content: rec.content,
        expected_revision: fresh.revision,
      });
      const after = await workbenchApi.readFile(workspaceId, targetPath);
      const currentHash = sha256Hex(after.content);
      const backupHash = metaString(rec, 'sha256');
      const result: HashVerify = backupHash
        ? { match: currentHash === backupHash, backupHash, currentHash, source: 'metadata' }
        : {
            match: currentHash === sha256Hex(rec.content),
            backupHash: null,
            currentHash,
            source: 'record-content',
          };
      setVerify(result);
      setNotice(result.match ? `回滚成功，内容哈希与备份一致（${targetPath}）` : `回滚已写盘，但哈希不一致（${targetPath}）`);
      setConfirmTarget(null);
      onRolledBack?.(); // 让工作台刷新编辑器缓冲区（重新读盘）
      await reload();
    } catch (e) {
      setError(errorMessage(e));
      setConfirmTarget(null);
    } finally {
      setRollingBack(false);
    }
  }

  // ------------------------------------------------------- 删除 / 清空 ----
  async function removeOne(id: string) {
    setError(null);
    try {
      await stashApi.remove(id);
      if (confirmTarget?.id === id) setConfirmTarget(null);
      await reload();
    } catch (e) {
      setError(errorMessage(e));
    }
  }

  async function clearAll() {
    if (!clearArmed) {
      setClearArmed(true);
      setTimeout(() => setClearArmed(false), 4000);
      return;
    }
    setClearArmed(false);
    setError(null);
    try {
      await stashApi.clear();
      setLastBackup(null);
      setVerify(null);
      await reload();
    } catch (e) {
      setError(errorMessage(e));
    }
  }

  if (!workspaceId || !path) {
    return (
      <div className="card backup-panel" data-testid="backup-panel">
        <div className="row spread">
          <strong>备份与回滚（P1-14）</strong>
        </div>
        <div className="muted">注册并选择工作区、选中一个文件后，可一键备份当前内容并随时回滚。</div>
      </div>
    );
  }

  return (
    <div className="card backup-panel" data-testid="backup-panel">
      <div className="row spread">
        <strong>备份与回滚（P1-14）</strong>
        <span className="muted small" title={path}>{path}</span>
      </div>

      <div className="row" style={{ marginTop: '0.4rem' }}>
        <button className="primary small" data-testid="backup-btn" onClick={() => void backupCurrentFile()} disabled={backingUp}>
          {backingUp ? '备份中…' : '备份当前文件'}
        </button>
        <button className="small" onClick={() => void reload()} disabled={loading}>
          {loading ? '刷新中…' : '刷新列表'}
        </button>
        <label className="muted small" style={{ display: 'inline-flex', alignItems: 'center', gap: '0.3rem' }}>
          <input type="checkbox" checked={onlyCurrent} onChange={(e) => setOnlyCurrent(e.target.checked)} />
          只看当前文件
        </label>
        <button className={`danger small${clearArmed ? ' armed' : ''}`} data-testid="clear-stash-btn" onClick={() => void clearAll()}>
          {clearArmed ? '再次点击确认清空' : '清空暂存区'}
        </button>
      </div>

      {error && <div className="error-text" role="alert">{error}</div>}
      {notice && <div className="notice ok" data-testid="backup-notice" role="status">{notice}</div>}

      {lastBackup && (
        <div className="muted small" data-testid="last-backup" style={{ marginTop: '0.3rem' }}>
          最近备份 <code>{lastBackup.id}</code> · sha256 <code data-testid="backup-hash">{lastBackup.sha256}</code>
        </div>
      )}

      {verify && (
        <div
          className={`hash-verify ${verify.match ? 'ok' : 'bad'}`}
          data-testid={verify.match ? 'hash-match' : 'hash-mismatch'}
          role="status"
        >
          {verify.match ? '✔ 哈希一致' : '✘ 哈希不一致'}（比对来源：{verify.source === 'metadata' ? '备份 metadata' : '记录内容'}）
          <div className="small">
            备份哈希 <code data-testid="verify-backup-hash">{verify.backupHash ?? '（未记录）'}</code>
          </div>
          <div className="small">
            回滚后哈希 <code data-testid="verify-current-hash">{verify.currentHash}</code>
          </div>
        </div>
      )}

      <div className="stash-list" data-testid="stash-list" style={{ marginTop: '0.5rem' }}>
        {pageRows.length === 0 && !loading && <div className="muted">暂无暂存记录{onlyCurrent ? '（当前文件）' : ''}。</div>}
        {pageRows.map((r) => (
          <div className="stash-row" key={r.id} data-testid="stash-row">
            <div className="row spread">
              <button
                className="linkish small"
                onClick={() => setExpandedId((cur) => (cur === r.id ? null : r.id))}
                title="点击查看/收起内容预览"
              >
                {r.title || '(无标题)'}
              </button>
              <span className="muted small">{r.created_at ? new Date(r.created_at).toLocaleString('zh-CN') : ''}</span>
            </div>
            <div className="row spread small">
              <span className="muted">
                {metaString(r, 'path') ?? '—'} · sha256 <code>{(metaString(r, 'sha256') ?? '').slice(0, 12) || '—'}…</code>
              </span>
              <span>
                <button className="primary small" data-testid={`rollback-btn-${r.id}`} onClick={() => setConfirmTarget(r)}>
                  回滚
                </button>{' '}
                <button className="danger small" onClick={() => void removeOne(r.id)}>
                  删除
                </button>
              </span>
            </div>
            {expandedId === r.id && (
              <pre className="stash-preview">{r.content.slice(0, 2000)}{r.content.length > 2000 ? '\n…（截断预览）' : ''}</pre>
            )}
          </div>
        ))}
      </div>

      {pageCount > 1 && (
        <div className="row spread" style={{ marginTop: '0.4rem' }}>
          <button className="small" disabled={safePage === 0} onClick={() => setPage(safePage - 1)}>上一页</button>
          <span className="muted small">第 {safePage + 1}/{pageCount} 页 · 共 {visible.length} 条</span>
          <button className="small" disabled={safePage >= pageCount - 1} onClick={() => setPage(safePage + 1)}>下一页</button>
        </div>
      )}

      {confirmTarget && (
        <div className="confirm-overlay" role="dialog" aria-modal="true" data-testid="rollback-confirm">
          <div className="confirm-box card">
            <strong>确认回滚？</strong>
            <div className="small" style={{ marginTop: '0.4rem' }}>
              将把暂存记录 <code>{confirmTarget.title}</code> 的内容写回文件{' '}
              <code data-testid="confirm-path">{metaString(confirmTarget, 'path') ?? path}</code>，
              当前文件的未保存修改会被覆盖（写回走现有保存链路）。
            </div>
            <div className="row" style={{ marginTop: '0.6rem' }}>
              <button className="danger small" data-testid="confirm-rollback-btn" onClick={() => void doRollback()} disabled={rollingBack}>
                {rollingBack ? '回滚中…' : '确认回滚'}
              </button>
              <button className="small" onClick={() => setConfirmTarget(null)} disabled={rollingBack}>
                取消
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
