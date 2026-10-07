/**
 * 统一保存状态指示器（A-基座质保-12 / W2）+ 基座接入壳 `BaseBound`（A-基座质保-11 / W1）。
 *
 * W2 四条验收：
 * ① 四态定义与配色、文案、位置由基座统一规范 —— 文案取自 `SAVE_STATE_LABELS` /
 *    `formatSavedAt`（唯一来源），配色只吃 tokens.css 的 `--ui-st-*` 状态色，
 *    位置由 `BaseBound` 统一放在内容区上方，页面不得自拟。
 * ② 可点开查看保存时间轴摘要、存储位置、占用空间 —— 「详情」面板。
 * ③ 保存失败态带可见提示并保留现场 —— 常驻 `role="alert"` 回执卡 + 「重试」，
 *    并显示排队改动条数（现场没被清掉）。
 * ④ 不阻断操作、不抢焦点 —— 不设 autoFocus / 不 trapFocus / 不遮罩、不 preventDefault；
 *    只有用户主动点「详情」才会展开。
 *
 * W4 的界面侧：降级（改存备用目录）时给出「降级·备用目录」徽标与回执提示，
 * 恢复后再自动收回——用户始终知道东西现在存在哪儿。
 */

import { useId, useState, type ReactNode } from 'react';

import {
  SAVE_STATE_LABELS,
  formatBytes,
  formatRelative,
  formatSavedAt,
  useBase,
  type SaveRecord,
  type SaveState,
  type StorageErrorReceipt,
  type StorageState,
} from '../../hooks/useAutosave';
import './saveStatusIndicator.css';

export interface SaveStatusIndicatorProps {
  state: SaveState;
  /** 上次保存时刻（毫秒）。null = 还没有过保存。 */
  savedAt?: number | null;
  /** 存储位置（给人看的路径 / 云盘名）。 */
  location?: string | null;
  /** 占用空间（字节）。 */
  bytes?: number | null;
  /** 目标盘可用空间（字节）；拿不到就是 null，界面显示「未知」。 */
  freeBytes?: number | null;
  directory?: string | null;
  /** 排队中（尚未落盘）的改动条数。 */
  pendingChanges?: number;
  storage?: StorageState;
  error?: StorageErrorReceipt | null;
  history?: SaveRecord[];
  onRetry?: (() => void) | undefined;
  /** 注入时钟（给相对时间用），便于测试。 */
  now?: number;
  className?: string;
}

export function SaveStatusIndicator({
  state, savedAt = null, location = null, bytes = null, freeBytes = null,
  directory = null, pendingChanges = 0, storage = 'ok', error = null, history = [],
  onRetry, now, className,
}: SaveStatusIndicatorProps) {
  const [open, setOpen] = useState(false);
  const panelId = useId();
  const clock = now ?? Date.now();

  const label = state === 'saved' ? formatSavedAt(savedAt, clock) : SAVE_STATE_LABELS[state];
  const degraded = storage === 'degraded';
  const rootClass = ['ui-save-status', `ui-save-status--${state}`, className]
    .filter(Boolean).join(' ');

  return (
    <div className={rootClass} data-state={state} data-storage={storage}>
      <span className="ui-save-status__dot" aria-hidden="true" />
      <span className="ui-save-status__label" role="status" aria-live="polite">{label}</span>

      {degraded ? (
        <span className="ui-save-status__badge" data-testid="save-degraded-badge">
          降级·备用目录
        </span>
      ) : null}

      {pendingChanges > 0 ? (
        <span className="ui-save-status__pending">排队 {pendingChanges} 处改动</span>
      ) : null}

      <button
        type="button"
        className="ui-save-status__toggle"
        aria-expanded={open}
        aria-controls={panelId}
        onClick={() => setOpen((v) => !v)}
      >
        {open ? '收起' : '详情'}
      </button>

      {open ? (
        <div className="ui-save-status__panel" id={panelId}>
          <dl className="ui-save-status__facts">
            <div>
              <dt>上次保存时间</dt>
              <dd>{savedAt == null ? '尚无记录' : `${new Date(savedAt).toLocaleString()}（${formatRelative(savedAt, clock)}）`}</dd>
            </div>
            <div>
              <dt>存储位置</dt>
              <dd>{location ?? directory ?? '本地（默认）'}</dd>
            </div>
            <div>
              <dt>占用空间</dt>
              <dd>{formatBytes(bytes)}</dd>
            </div>
            <div>
              <dt>目标盘可用空间</dt>
              <dd>{formatBytes(freeBytes)}</dd>
            </div>
          </dl>
          <p className="ui-save-status__timeline">
            保存时间轴：
            {history.length === 0
              ? '尚无记录'
              : history
                .slice(-5)
                .map((r) => `${formatRelative(r.at, clock)}·${r.label}`)
                .join('　|　')}
          </p>
        </div>
      ) : null}

      {error && (state === 'error' || degraded) ? (
        <div className="ui-save-status__error" role="alert">
          <strong>{error.message}</strong>
          <span className="ui-save-status__hint">{error.hint}</span>
          {onRetry ? (
            <button type="button" className="ui-save-status__retry" onClick={onRetry}>
              重试
            </button>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}

export interface BaseBoundProps extends SaveStatusIndicatorProps {
  /** 界面标识（工作台 / 个人空间 / 标签页名）。 */
  surface: string;
  /** 声明的能力子集；省略 = 全部四项（与后端扫描口径一致）。 */
  capabilities?: readonly string[];
  children: ReactNode;
}

/**
 * 基座接入壳：页面用 `<BaseBound surface="…">…</BaseBound>` 包住内容，
 * 即完成「接入声明 + 统一状态指示器」两件事（后端按这个标记判定已接入）。
 */
export function BaseBound({
  surface, capabilities, children, className, ...status
}: BaseBoundProps) {
  const base = useBase({ surface, capabilities });
  const classes = ['ui-base-bound', className].filter(Boolean).join(' ');
  return (
    <div
      className={classes}
      data-base-bound="true"
      data-base-surface={surface}
      data-base-capabilities={base.capabilities.join(',')}
    >
      <div className="ui-base-bound__status">
        <SaveStatusIndicator {...status} />
      </div>
      <div className="ui-base-bound__body">{children}</div>
    </div>
  );
}
