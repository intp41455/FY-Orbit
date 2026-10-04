import type { SpotStatus, ThemeId } from './balance';
import { THEME_LABELS, formatCooldown, formatNumber, sortSpotsForDisplay } from './balance';

/**
 * W2 · 探险点面板
 *
 * 展示服务端下发的探险点状态（可用 / 冷却中 / 蒙尘），点击即请求采集。
 * **前端不做任何产出判定**：点亮、冷却、产出数量全部来自
 * `GET /api/cabin/save` 的 `spots[]`；点击只是发一个 `explore` 动作，
 * 成功与否由后端 200 / 409 决定。
 *
 * 诚实原则：
 *   - 冷却中的点禁用并显示剩余时间，不用「假装可点」骗玩家；
 *   - 采集失败（如刚采过）时由父级展示后端原文，不静默忽略；
 *   - 灰尘点如实标注「落灰了」，因为它需要「打扫」而非「采集」才能消失。
 */

export interface SpotsPanelProps {
  spots: readonly SpotStatus[];
  theme: ThemeId;
  busy: boolean;
  /** 该探险点是否有待结算的照料（浇水后产出翻倍），由父级从动作回执传入。 */
  onExplore: (spotId: string) => void;
  onWater?: (spotId: string) => void;
  onClean?: (spotId: string) => void;
}

function spotTestId(spotId: string): string {
  return `w2-spot-${spotId}`;
}

export function SpotsPanel({
  spots,
  theme,
  busy,
  onExplore,
  onWater,
  onClean,
}: SpotsPanelProps) {
  const ordered = sortSpotsForDisplay(spots);
  const readyCount = ordered.filter((s) => s.available).length;

  return (
    <section className="w2-panel" aria-label="背景探险" data-testid="w2-spots-panel">
      <header className="w2-panel-head">
        <h3 className="w2-panel-title">🧭 {THEME_LABELS[theme] ?? theme} · 探险点</h3>
        <span className="w2-panel-meta" data-testid="w2-spots-ready">
          可采 {readyCount} / {ordered.length}
        </span>
      </header>

      {ordered.length === 0 ? (
        <p className="w2-empty" data-testid="w2-spots-empty">
          这个主题还没有探险点（服务端未返回数据）。
        </p>
      ) : (
        <ul className="w2-spot-grid">
          {ordered.map((spot) => {
            const cooldown = formatCooldown(spot.remaining_seconds);
            const disabled = busy || !spot.available;
            return (
              <li
                key={spot.id}
                className={spot.available ? 'w2-spot ready' : 'w2-spot cooling'}
                data-testid={spotTestId(spot.id)}
                data-available={spot.available ? 'true' : 'false'}
              >
                <div className="w2-spot-top">
                  <span className="w2-spot-label">{spot.label}</span>
                  {spot.dust && (
                    <span className="w2-chip warn" data-testid={`${spotTestId(spot.id)}-dust`}>
                      落灰了
                    </span>
                  )}
                </div>

                <div className="w2-spot-yield">
                  <span title={spot.material_label}>{spot.material_label} ×{spot.qty}</span>
                  <span>{formatNumber(spot.coins)} 金币</span>
                  <span className="w2-spot-intimacy">亲密 +{spot.intimacy}</span>
                </div>

                <div className="w2-spot-foot">
                  {spot.available ? (
                    <span className="w2-cooldown ready" data-testid={`${spotTestId(spot.id)}-state`}>
                      可采集
                    </span>
                  ) : (
                    <span className="w2-cooldown" data-testid={`${spotTestId(spot.id)}-state`}>
                      冷却中 · 剩 {cooldown ?? '—'}
                    </span>
                  )}
                  {spot.collect_count > 0 && (
                    <span className="w2-spot-count">已采 {spot.collect_count} 次</span>
                  )}
                </div>

                <div className="w2-spot-actions">
                  <button
                    type="button"
                    className="w2-btn primary"
                    data-testid={`${spotTestId(spot.id)}-explore`}
                    disabled={disabled}
                    aria-disabled={disabled}
                    onClick={() => onExplore(spot.id)}
                  >
                    {busy ? '结算中…' : '采集'}
                  </button>
                  {onWater && (
                    <button
                      type="button"
                      className="w2-btn"
                      data-testid={`${spotTestId(spot.id)}-water`}
                      disabled={busy}
                      onClick={() => onWater(spot.id)}
                    >
                      💧 浇水
                    </button>
                  )}
                  {onClean && spot.dust && (
                    <button
                      type="button"
                      className="w2-btn"
                      data-testid={`${spotTestId(spot.id)}-clean`}
                      disabled={busy}
                      onClick={() => onClean(spot.id)}
                    >
                      🧹 打扫
                    </button>
                  )}
                </div>
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
}
