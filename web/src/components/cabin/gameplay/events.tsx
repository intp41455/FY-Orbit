import type { EventView } from './balance';
import { formatEventReward, isNothingEvent } from './balance';

/**
 * W2 · 随机事件弹层
 *
 * DNA-8「即时反馈」：每次采集后 ≤100ms 弹出结算卡，明确说清
 * 「+了什么 / 为什么 / 没拿到什么」。
 *
 * 诚实原则（本组件存在的全部意义）：
 *   - 「一无所获」必须**如实展示为「什么也没有」**，不换成安慰性假文案；
 *   - 没拿到奖励时 `formatEventReward` 返回 null，这里就显示「无额外奖励」，
 *     绝不显示 0 金币之类的假数字；
 *   - 事件文案是后端按性格生成/池选的结果，前端原样展示，不二次润色。
 */

export interface EventCardProps {
  event: EventView | null | undefined;
  /** 采集回执里的即时反馈语（后端 `feedback` 字段）。 */
  feedback?: string | null;
  onDismiss?: () => void;
}

export function EventCard({ event, feedback, onDismiss }: EventCardProps) {
  if (!event) return null;

  const nothing = isNothingEvent(event);
  const reward = formatEventReward(event);

  return (
    <aside
      className={nothing ? 'w2-event-card nothing' : 'w2-event-card'}
      role="status"
      data-testid="w2-event-card"
      data-nothing={nothing ? 'true' : 'false'}
    >
      <div className="w2-event-head">
        <span className="w2-event-title">
          {nothing ? '🍃 这次什么也没有' : (event.title ?? '✨ 遇到一件事')}
        </span>
        {onDismiss && (
          <button
            type="button"
            className="w2-btn ghost tiny"
            data-testid="w2-event-close"
            onClick={onDismiss}
            aria-label="关闭事件"
          >
            ✕
          </button>
        )}
      </div>

      <p className="w2-event-text" data-testid="w2-event-text">
        {event.text || '（服务端没有返回事件文案）'}
      </p>

      {nothing ? (
        <p className="w2-event-reward none" data-testid="w2-event-reward">
          无额外奖励
        </p>
      ) : reward ? (
        <p className="w2-event-reward" data-testid="w2-event-reward">
          {reward}
        </p>
      ) : null}

      {feedback && (
        <p className="w2-event-feedback" data-testid="w2-event-feedback">
          {feedback}
        </p>
      )}
    </aside>
  );
}

/**
 * 宠物自主行为条（DNA-5 自主生命感）。
 *
 * 后端按「离开时长 + 亲密度」roll 出「刚才干了什么」，并可能留下小玩意。
 * 前端只展示；`unlocked_count` 用来诚实说明「还有 N 种行为等你解锁」，
 * 不谎称已看全所有行为。
 */
export interface CompanionBarProps {
  behavior: string;
  label: string;
  awayHours: number;
  trinkets: readonly string[];
  unlockedCount: number;
}

export function CompanionBar({
  behavior,
  label,
  awayHours,
  trinkets,
  unlockedCount,
}: CompanionBarProps) {
  return (
    <div className="w2-companion" data-testid="w2-companion" data-behavior={behavior}>
      <span className="w2-companion-emoji" aria-hidden="true">
        🐾
      </span>
      <div className="w2-companion-body">
        <p className="w2-companion-text">
          {label || '在旁边待着'}
          {awayHours >= 0.25 && (
            <span className="w2-companion-away">（你离开了 {awayHours.toFixed(1)} 小时）</span>
          )}
        </p>
        {trinkets.length > 0 && (
          <p className="w2-companion-trinkets" data-testid="w2-companion-trinkets">
            留下了：{trinkets.join('、')}
          </p>
        )}
        <p className="w2-companion-progress" data-testid="w2-companion-unlocked">
          已解锁 {unlockedCount} 种行为 · 多陪它才会解锁新的
        </p>
      </div>
    </div>
  );
}

/**
 * 离线收益条。
 *
 * `capped=true` 时必须如实说明「按 24 小时上限结算」，
 * 不让玩家以为挂机越久收益无限增长。
 */
export interface OfflineBarProps {
  awayHours: number;
  realAwayHours: number;
  capped: boolean;
  refreshedSpots: readonly string[];
  text: string;
  onDismiss?: () => void;
}

export function OfflineBar({
  awayHours,
  realAwayHours,
  capped,
  refreshedSpots,
  text,
  onDismiss,
}: OfflineBarProps) {
  return (
    <aside className="w2-offline" role="status" data-testid="w2-offline-bar">
      <span className="w2-offline-icon" aria-hidden="true">
        🌙
      </span>
      <div className="w2-offline-body">
        <p className="w2-offline-text">{text}</p>
        {capped && (
          <p className="w2-offline-cap" data-testid="w2-offline-capped">
            实际离开 {realAwayHours.toFixed(1)} 小时，已按 24 小时上限结算为 {awayHours.toFixed(1)} 小时
          </p>
        )}
        {refreshedSpots.length > 0 && (
          <p className="w2-offline-spots" data-testid="w2-offline-spots">
            已刷新：{refreshedSpots.join('、')}
          </p>
        )}
      </div>
      {onDismiss && (
        <button
          type="button"
          className="w2-btn ghost tiny"
          data-testid="w2-offline-close"
          onClick={onDismiss}
          aria-label="关闭离线提示"
        >
          ✕
        </button>
      )}
    </aside>
  );
}
