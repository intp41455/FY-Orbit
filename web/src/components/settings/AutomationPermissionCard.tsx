/**
 * W10-B · GUI 自动化权限面板（设置页独立卡片）。
 *
 * 诚实原则（任务书铁律）：
 *  - 默认「关闭」，打开任何更高档位都由用户显式选择；
 *  - 「完全控制」= 可点击/打字，是短时效注入权，必须带 TTL，到期自动回落关闭；
 *  - 明确文案告知「重启服务后回到默认关闭」，不做常驻误导。
 */
import { useEffect, useState } from 'react';
import {
  AutomationMode,
  fetchAutomationPermissions,
  putAutomationPermissions,
} from '../../api/automation';
import { useAsync, Spinner, errorMessage } from '../ui';
import { useBase } from '../../hooks/useAutosave';

const MODES: Array<{ id: AutomationMode; label: string; desc: string }> = [
  { id: 'off', label: '关闭', desc: '不允许任何截图/鼠标/键盘操作（默认）' },
  { id: 'readonly', label: '只读', desc: '允许截图与列出窗口，不移动指针、不输入' },
  { id: 'safe', label: '安全', desc: '在只读基础上允许移动鼠标，不点击、不打字' },
  { id: 'full', label: '完全控制', desc: '允许点击与打字（短时效，到期自动回落关闭）' },
];

const TTL_CHOICES = [60, 300, 600];

export function AutomationPermissionCard() {
  useBase({ surface: 'web/src/components/settings/AutomationPermissionCard' });
  const { data, loading, error, reload } = useAsync(() => fetchAutomationPermissions(), []);
  const [pending, setPending] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  const [formError, setFormError] = useState<string | null>(null);
  const [ttl, setTtl] = useState(60);

  // 本地倒计时：服务端返回的剩余秒数，每秒递减；归零即视为已回落。
  const [localLeft, setLocalLeft] = useState<number | null>(null);
  useEffect(() => {
    if (data?.mode === 'full' && data.ttl_remaining_seconds) {
      setLocalLeft(data.ttl_remaining_seconds);
      return;
    }
    setLocalLeft(null);
  }, [data?.mode, data?.ttl_remaining_seconds]);
  useEffect(() => {
    if (localLeft === null || localLeft <= 0) return;
    const t = window.setTimeout(() => setLocalLeft((v) => (v === null ? null : v - 1)), 1000);
    return () => window.clearTimeout(t);
  }, [localLeft]);

  const current: AutomationMode = data?.mode ?? 'off';

  async function choose(mode: AutomationMode) {
    setPending(true);
    setMsg(null);
    setFormError(null);
    try {
      await putAutomationPermissions(mode, mode === 'full' ? ttl : undefined);
      setMsg(
        mode === 'full'
          ? `已开启完全控制（${ttl}s 后自动回落关闭）。重启服务后同样回到默认关闭。`
          : mode === 'off'
            ? '已关闭 GUI 自动化。'
            : `已切换到「${MODES.find((m) => m.id === mode)?.label}」档。`,
      );
      await reload();
    } catch (e) {
      setFormError(errorMessage(e));
    } finally {
      setPending(false);
    }
  }

  return (
    <div className="card" id="automation-permissions" data-testid="automation-perm-card">
      <h3 style={{ marginTop: 0 }}>桌面自动化权限</h3>
      <p className="muted">
        控制助手是否能对你的真实桌面截图、移动鼠标、点击与打字。这是高风险注入权，
        <strong>默认关闭</strong>；「完全控制」为短时效放行，到期自动回落，重启服务后回到默认关闭。
      </p>

      {loading && <Spinner />}
      {error && <div className="notice danger" role="alert">{errorMessage(error)}</div>}

      {data && (
        <>
          <table>
            <tbody>
              <tr>
                <td>当前档位</td>
                <td data-testid="automation-current-mode">
                  <span className="badge warn">{MODES.find((m) => m.id === current)?.label ?? current}</span>
                  {current === 'full' && (
                    <span className="badge ok" data-testid="automation-ttl" style={{ marginLeft: '0.5rem' }}>
                      剩余 {Math.max(0, localLeft ?? 0)}s
                    </span>
                  )}
                </td>
              </tr>
            </tbody>
          </table>

          <div className="field" style={{ marginTop: '0.8rem' }}>
            {MODES.map((m) => (
              <label
                key={m.id}
                style={{ display: 'flex', gap: '0.5rem', alignItems: 'flex-start', marginBottom: '0.4rem' }}
              >
                <input
                  type="radio"
                  name="automation-mode"
                  data-testid={`automation-mode-${m.id}`}
                  checked={current === m.id}
                  disabled={pending}
                  onChange={() => void choose(m.id)}
                />
                <span>
                  <strong>{m.label}</strong>
                  <span className="muted"> — {m.desc}</span>
                </span>
              </label>
            ))}
          </div>

          {current !== 'off' && (
            <div className="field">
              <label htmlFor="automation-ttl-select">完全控制时长（仅切到「完全控制」时生效）</label>
              <select
                id="automation-ttl-select"
                data-testid="automation-ttl-select"
                value={ttl}
                onChange={(e) => setTtl(Number(e.target.value))}
              >
                {TTL_CHOICES.map((s) => (
                  <option key={s} value={s}>{s} 秒</option>
                ))}
              </select>
            </div>
          )}

          {formError && <div className="notice danger" role="alert" data-testid="automation-error">{formError}</div>}
          {msg && <div className="notice info" data-testid="automation-msg" style={{ marginTop: '0.6rem' }}>{msg}</div>}
          <p className="small-text" style={{ marginTop: '0.6rem' }}>{data.honesty_note}</p>
        </>
      )}
    </div>
  );
}
