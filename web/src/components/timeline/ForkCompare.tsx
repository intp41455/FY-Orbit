import type { CompareResult } from '../../api/archiveFork';

/**
 * P4 · 分支对比视图（A-存档回溯-05）。
 *
 * 纯展示：把后端逐键比对的结果分区呈现。
 * 诚实原则——**同一性判定以后端 ``identical`` 为准**，本组件不自行重算，
 * 避免视图与数据两侧判据不一致。
 */

interface Props {
  result: CompareResult | null;
}

function ValueCell({ value }: { value: unknown }) {
  const text =
    typeof value === 'string' ? value : JSON.stringify(value ?? null);
  return (
    <code
      style={{
        fontSize: 11,
        background: 'var(--glass-base, #f8fafc)',
        border: '1px solid var(--line, #e2e8f0)',
        borderRadius: 6,
        padding: '2px 6px',
        wordBreak: 'break-all',
      }}
    >
      {text}
    </code>
  );
}

export function ForkCompare({ result }: Props) {
  if (!result) {
    return (
      <p style={{ color: 'var(--text-faint, #94a3b8)', fontSize: 13, padding: 12 }}>
        选一个分支并点「对比」后，这里显示两侧执行结果的逐键差异。
      </p>
    );
  }

  const s = result.summary;
  return (
    <div data-testid="fork-compare" style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
        <strong style={{ color: 'var(--text-main, #0f172a)', fontSize: 13 }}>
          {result.left_label} ↔ {result.right_label}
        </strong>
        <span
          data-testid="compare-identical"
          style={{
            fontSize: 12,
            fontWeight: 700,
            color: result.identical ? '#16a34a' : 'var(--rose, #e11d48)',
          }}
        >
          {result.identical ? '完全一致' : '存在差异'}
        </span>
      </div>

      <div style={{ display: 'flex', gap: 14, flexWrap: 'wrap', fontSize: 12 }}>
        <span>共 {s.total_keys} 键</span>
        <span style={{ color: '#16a34a' }}>相同 {s.same}</span>
        <span style={{ color: 'var(--amber, #d97706)' }}>变更 {s.changed}</span>
        <span style={{ color: 'var(--sky, #0284c7)' }}>仅左 {s.only_left}</span>
        <span style={{ color: 'var(--violet, #7c3aed)' }}>仅右 {s.only_right}</span>
      </div>

      {result.changed.length > 0 && (
        <section data-testid="compare-changed">
          <h4 style={{ margin: '0 0 6px', fontSize: 12, color: 'var(--text-main, #0f172a)' }}>
            值变更
          </h4>
          <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12 }}>
            <tbody>
              {result.changed.map((c) => (
                <tr key={c.key} style={{ borderTop: '1px solid var(--line, #e2e8f0)' }}>
                  <td style={{ padding: '4px 6px', color: 'var(--text-muted, #64748b)', width: '28%' }}>
                    {c.key}
                  </td>
                  <td style={{ padding: '4px 6px' }}><ValueCell value={c.left} /></td>
                  <td style={{ padding: '4px 6px' }}><ValueCell value={c.right} /></td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      )}

      {result.only_left.length > 0 && (
        <section data-testid="compare-only-left">
          <h4 style={{ margin: '0 0 6px', fontSize: 12 }}>仅左侧有（{result.only_left.length}）</h4>
          <div style={{ fontSize: 11, color: 'var(--text-muted, #64748b)' }}>
            {result.only_left.join('、')}
          </div>
        </section>
      )}

      {result.only_right.length > 0 && (
        <section data-testid="compare-only-right">
          <h4 style={{ margin: '0 0 6px', fontSize: 12 }}>仅右侧有（{result.only_right.length}）</h4>
          <div style={{ fontSize: 11, color: 'var(--text-muted, #64748b)' }}>
            {result.only_right.join('、')}
          </div>
        </section>
      )}
    </div>
  );
}
