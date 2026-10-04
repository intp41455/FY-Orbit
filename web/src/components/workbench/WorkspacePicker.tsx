import { useState } from 'react';
import { workbenchApi, type WorkspaceSummary } from '../../api/workbench';
import { errorMessage } from '../ui';

interface Props {
  workspaces: WorkspaceSummary[];
  selectedId: string | null;
  onSelect: (id: string) => void;
  onChanged: () => void;
}

export function WorkspacePicker({ workspaces, selectedId, onSelect, onChanged }: Props) {
  const [showForm, setShowForm] = useState(false);
  const [projectName, setProjectName] = useState('');
  const [root, setRoot] = useState('');
  const [mode, setMode] = useState('local');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function register() {
    if (!projectName.trim() || !root.trim()) return;
    setBusy(true);
    setError(null);
    try {
      await workbenchApi.registerWorkspace({
        project_name: projectName.trim(),
        authorized_root: root.trim(),
        mode,
        data_domain: 'work',
      });
      setProjectName('');
      setRoot('');
      setShowForm(false);
      onChanged();
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="card">
      <div className="row spread">
        <strong>工作区</strong>
        <button className="small" onClick={() => setShowForm((v) => !v)}>
          {showForm ? '取消' : '注册工作区'}
        </button>
      </div>

      {workspaces.length === 0 && !showForm && (
        <div className="muted" style={{ margin: '0.5rem 0' }}>尚无已注册工作区。</div>
      )}

      <div className="workspace-list">
        {workspaces.map((w) => (
          <button
            key={w.id}
            className={`workspace-chip ${selectedId === w.id ? 'active' : ''}`}
            onClick={() => onSelect(w.id)}
            title={w.authorized_root}
          >
            <strong>{w.project_name}</strong>
            <span className="muted">{w.branch || '—'}</span>
            <span className="badge">{w.state}</span>
          </button>
        ))}
      </div>

      {showForm && (
        <div className="field-stack" style={{ marginTop: '0.6rem' }}>
          <label>项目名称
            <input value={projectName} onChange={(e) => setProjectName(e.target.value)} placeholder="my-project" />
          </label>
          <label>授权根目录（绝对路径）
            <input value={root} onChange={(e) => setRoot(e.target.value)} placeholder="C:\\Users\\me\\code\\my-project" />
          </label>
          <label>模式
            <select value={mode} onChange={(e) => setMode(e.target.value)}>
              <option value="local">local</option>
              <option value="cloud">cloud</option>
            </select>
          </label>
          <button className="primary small" onClick={() => void register()} disabled={busy || !projectName.trim() || !root.trim()}>
            {busy ? '注册中…' : '注册'}
          </button>
          {error && <div className="error-text" role="alert">{error}</div>}
        </div>
      )}
    </div>
  );
}
