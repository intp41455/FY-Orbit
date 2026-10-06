import { useState } from 'react';

/**
 * P4 · 分叉创建表单（A-存档回溯-04「改参重跑生成新分支」）。
 *
 * 让用户指定：源 thread / 源存档点 / 参数覆盖（JSON）/ 分支标签。
 * 提交后由上层调用 createFork，**原历史不动**——这是需求原文的红线，
 * 表单文案明确提示，避免用户误以为会覆盖。
 */

interface Props {
  defaultThreadId?: string;
  onSubmit: (input: {
    source_thread_id: string;
    source_checkpoint_id: string;
    overrides: Record<string, unknown>;
    label: string;
  }) => void;
  disabled?: boolean;
}

export function ForkForm({ defaultThreadId = '', onSubmit, disabled = false }: Props) {
  const [threadId, setThreadId] = useState(defaultThreadId);
  const [checkpointId, setCheckpointId] = useState('');
  const [overridesText, setOverridesText] = useState('{}');
  const [label, setLabel] = useState('');
  const [error, setError] = useState('');

  const handleSubmit = () => {
    if (!threadId.trim()) {
      setError('源 thread 不能为空');
      return;
    }
    let overrides: Record<string, unknown> = {};
    const raw = overridesText.trim();
    if (raw && raw !== '{}') {
      try {
        const parsed = JSON.parse(raw) as unknown;
        if (parsed === null || typeof parsed !== 'object' || Array.isArray(parsed)) {
          setError('参数覆盖必须是 JSON 对象，例如 {"temperature": 0.2}');
          return;
        }
        overrides = parsed as Record<string, unknown>;
      } catch {
        setError('参数覆盖不是合法 JSON');
        return;
      }
    }
    setError('');
    onSubmit({
      source_thread_id: threadId.trim(),
      source_checkpoint_id: checkpointId.trim(),
      overrides,
      label: label.trim(),
    });
  };

  const inputStyle = {
    width: '100%',
    padding: '6px 8px',
    borderRadius: 8,
    border: '1px solid var(--line, #e2e8f0)',
    fontSize: 13,
    background: 'var(--glass-base, #fff)',
    color: 'var(--text-main, #0f172a)',
    boxSizing: 'border-box' as const,
  };

  return (
    <div data-testid="fork-form" style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
      <label style={{ fontSize: 12, color: 'var(--text-muted, #64748b)' }}>
        源 thread（被回溯的存档所在线程）
        <input
          data-testid="fork-thread-input"
          value={threadId}
          onChange={(e) => setThreadId(e.target.value)}
          placeholder="例如 eval-thread-01"
          style={{ ...inputStyle, marginTop: 4 }}
        />
      </label>

      <label style={{ fontSize: 12, color: 'var(--text-muted, #64748b)' }}>
        源存档点 id（留空 = 该线程最新检查点）
        <input
          data-testid="fork-checkpoint-input"
          value={checkpointId}
          onChange={(e) => setCheckpointId(e.target.value)}
          placeholder="可留空"
          style={{ ...inputStyle, marginTop: 4 }}
        />
      </label>

      <label style={{ fontSize: 12, color: 'var(--text-muted, #64748b)' }}>
        参数覆盖（JSON，改的就是这里）
        <textarea
          data-testid="fork-overrides-input"
          value={overridesText}
          onChange={(e) => setOverridesText(e.target.value)}
          rows={3}
          style={{ ...inputStyle, marginTop: 4, fontFamily: 'monospace', resize: 'vertical' }}
        />
      </label>

      <label style={{ fontSize: 12, color: 'var(--text-muted, #64748b)' }}>
        分支标签（可选，便于时间线识别）
        <input
          data-testid="fork-label-input"
          value={label}
          onChange={(e) => setLabel(e.target.value)}
          placeholder="例如 低温度重跑"
          style={{ ...inputStyle, marginTop: 4 }}
        />
      </label>

      {error && (
        <p data-testid="fork-form-error" style={{ color: 'var(--rose, #e11d48)', fontSize: 12, margin: 0 }}>
          {error}
        </p>
      )}

      <p style={{ margin: 0, fontSize: 11, color: 'var(--text-faint, #94a3b8)' }}>
        分叉只新增分支，<strong>不覆盖原历史</strong>；原 thread 的存档一字不改。
      </p>

      <button
        type="button"
        data-testid="fork-submit"
        disabled={disabled}
        onClick={handleSubmit}
        style={{
          padding: '7px 12px',
          borderRadius: 10,
          border: '1px solid var(--ice-soft, #bae6fd)',
          background: 'var(--sky, #0284c7)',
          color: '#fff',
          fontSize: 13,
          fontWeight: 600,
          cursor: disabled ? 'not-allowed' : 'pointer',
        }}
      >
        生成新分支（改参重跑）
      </button>
    </div>
  );
}
