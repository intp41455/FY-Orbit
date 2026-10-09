/**
 * 任务看板页（A-任务看板-01～13 前端面）。
 *
 * 三视图同源（需求 08/09/10）：
 * - **看板视图**：按状态分列 + **真实 HTML5 拖拽**改状态（需求 08 的验收标准
 *   就是「支持看板拖拽改变任务状态」，所以拖拽不是可选装饰）；
 * - **列表视图**：同一份 board 数据排成表，可按时间/优先级排序（需求 09）；
 * - **详情抽屉**：子任务清单 / 关键事项 / 依赖 / 历史 / 操作（需求 10）。
 *
 * 设计红线（§2.7）：零纯绿、状态色只用 `--ui-st-*` 九档令牌、命中区 ≥44px、
 * `prefers-reduced-motion` 下无动画、窄屏不横向滚动。样式全在 kanban.css。
 *
 * 诚实性：后端 `red_band` 是唯一红带来源，前端不自行推导；`cancelled_count`
 * 单独显示而不是从板上悄悄消失；甘特未排期显示「未排期」；燃尽样本不足显示
 * 「数据不足」。
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  COLUMN_META,
  board as fetchBoard,
  boardBurndown as fetchBurndown,
  kanbanErrorCode,
  runOperation,
  setProgress,
  startTask,
  taskDetail,
  type BoardResponse,
  type BurndownResponse,
  type KanbanCard,
  type KanbanColumnId,
  type KanbanOperation,
  type TaskDetailResponse,
} from '../api/kanban';
import { BurndownChart, GanttChart, ProgressDonut, buildGanttRows, donutToneFor } from '../components/kanban/KanbanCharts';
import '../styles/pages/kanban.css';
import { BaseBound } from '../components/ui/SaveStatusIndicator';

type ViewId = 'board' | 'list' | 'timeline';

const VIEWS: Array<{ id: ViewId; label: string }> = [
  { id: 'board', label: '看板' },
  { id: 'list', label: '列表' },
  { id: 'timeline', label: '时间线' },
];

/** 状态 → 看板列。与后端 `_STATUS_TO_COLUMN` 对齐，拖拽落点据此校验。 */
const STATUS_TO_COLUMN: Record<string, KanbanColumnId> = {
  queued: 'todo',
  running: 'doing',
  waiting_input: 'blocked',
  waiting_approval: 'blocked',
  failed: 'blocked',
  completed: 'done',
};

type SortKey = 'updated' | 'deadline' | 'progress' | 'priority';

/** 拖拽落点只允许落到「确实能由 API 达成的列」，否则拒绝而不是假装成功。 */
const DRAG_TARGET_STATUS: Record<KanbanColumnId, string[]> = {
  todo: ['queued'],
  doing: ['running'],
  blocked: ['waiting_input'],
  done: [],
};

export function KanbanPage() {
  const [data, setData] = useState<BoardResponse | null>(null);
  const [burn, setBurn] = useState<BurndownResponse | null>(null);
  const [view, setView] = useState<ViewId>('board');
  const [sortKey, setSortKey] = useState<SortKey>('priority');
  const [detail, setDetail] = useState<TaskDetailResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [dragOver, setDragOver] = useState<KanbanColumnId | null>(null);
  const dragId = useRef<string | null>(null);

  const load = useCallback(async () => {
    try {
      setData(await fetchBoard());
      setError(null);
    } catch (e) {
      setError(kanbanErrorCode(e));
    }
  }, []);

  useEffect(() => {
    void load();
    void fetchBurndown(14).then(setBurn).catch(() => setBurn(null));
  }, [load]);

  const cards = useMemo(
    () => (data ? data.columns.flatMap((c) => c.cards) : []),
    [data],
  );

  const sorted = useMemo(() => {
    const list = [...cards];
    if (sortKey === 'updated') {
      list.sort((a, b) => (b.updated_at ?? '').localeCompare(a.updated_at ?? ''));
    } else if (sortKey === 'deadline') {
      // 没设截止的排最后，不假装它最紧急
      list.sort((a, b) => {
        if (!a.deadline && !b.deadline) return 0;
        if (!a.deadline) return 1;
        if (!b.deadline) return -1;
        return a.deadline.localeCompare(b.deadline);
      });
    } else if (sortKey === 'progress') {
      list.sort((a, b) => b.weighted_progress - a.weighted_progress);
    } else {
      list.sort(
        (a, b) =>
          Number(b.critical) - Number(a.critical) ||
          Number(b.red_band) - Number(a.red_band) ||
          b.weighted_progress - a.weighted_progress,
      );
    }
    return list;
  }, [cards, sortKey]);

  const openDetail = useCallback(async (taskId: string) => {
    try {
      setDetail(await taskDetail(taskId));
    } catch (e) {
      setError(kanbanErrorCode(e));
    }
  }, []);

  const operate = useCallback(
    async (taskId: string, op: KanbanOperation, reason = '') => {
      setBusy(true);
      try {
        await runOperation(taskId, op, reason);
        setError(null);
        await load();
        await openDetail(taskId);
      } catch (e) {
        setError(kanbanErrorCode(e));
      } finally {
        setBusy(false);
      }
    },
    [load, openDetail],
  );

  const onStart = useCallback(
    async (taskId: string) => {
      setBusy(true);
      try {
        await startTask(taskId);
        setError(null);
        await load();
      } catch (e) {
        // dependency_blocking 是真实业务结果，要让用户看见是谁挡着
        setError(kanbanErrorCode(e));
      } finally {
        setBusy(false);
      }
    },
    [load],
  );

  // ---- 拖拽（需求 08：拖拽改状态） ----
  const onDrop = useCallback(
    async (column: KanbanColumnId) => {
      const id = dragId.current;
      dragId.current = null;
      setDragOver(null);
      if (!id) return;
      const card = cards.find((c) => c.id === id);
      if (!card) return;
      const target = STATUS_TO_COLUMN[card.status];
      if (target === column) return;                       // 原地放下，什么都不做
      const reachable = DRAG_TARGET_STATUS[column];
      if (!reachable.length) {
        // 「完成」列只能由工作流引擎判定，看板不许手工拖进去
        setError('column_not_reachable');
        return;
      }
      setBusy(true);
      try {
        if (column === 'doing') await startTask(id);
        else await runOperation(id, 'pause', '');
        setError(null);
        await load();
      } catch (e) {
        setError(kanbanErrorCode(e));
      } finally {
        setBusy(false);
      }
    },
    [cards, load],
  );

  if (error) {
    return (
      <div className="kb-page" data-testid="kb-page">
        <div className="kb-error" role="alert" data-testid="kb-error">
          操作未完成：<code>{error}</code>
        </div>
        <button type="button" className="ui-btn" onClick={() => void load()}>
          重试
        </button>
      </div>
    );
  }

  if (!data) {
    return (
      <div className="kb-page" data-testid="kb-page">
        <p className="ui-hint" data-testid="kb-loading">
          加载中…
        </p>
      </div>
    );
  }

  const { summary } = data;

  return (
    <BaseBound surface="kanban" state="idle">
      <div className="kb-page" data-testid="kb-page">
        <header className="kb-top">
          <h1 className="kb-title">任务看板</h1>
          <div
            className="kb-summary"
            data-testid="kb-summary"
            data-total={summary.total_cards}
            data-red={summary.red_band_count}
          >
            <span className="kb-chip">
              任务 <b>{summary.total_cards}</b>
            </span>
            <span className="kb-chip">
              加权进度 <b>{summary.weighted_progress}%</b>
            </span>
            {summary.red_band_count > 0 && (
              <span className="kb-chip kb-chip--red" data-testid="kb-redband-chip">
                红带 <b>{summary.red_band_count}</b>
              </span>
            )}
            {summary.cancelled_count > 0 && (
              <span
                className="kb-chip kb-chip--muted"
                data-testid="kb-cancelled-chip"
                title="已终止，不上板但必须被看见"
              >
                已终止 <b>{summary.cancelled_count}</b>
              </span>
            )}
          </div>
        </header>

        {/* 红带（需求 11）：常驻顶部，只由后端真实信号点亮 */}
        {cards.filter((c) => c.red_band).length > 0 && (
          <section className="kb-redband" role="region" aria-label="阻塞红带告警" data-testid="kb-redband">
            {cards
              .filter((c) => c.red_band)
              .map((c) => (
                <div key={c.id} className="kb-redband__row" data-testid={`kb-redband-${c.id}`}>
                  <span className="kb-redband__title">{c.goal}</span>
                  <span className="kb-redband__reasons">
                    {(c.red_band_detail?.reasons ?? []).map((r, i) => (
                      <span key={i} className="kb-redband__reason">
                        {r.detail}
                      </span>
                    ))}
                  </span>
                  <span className="kb-redband__meta">
                    {c.blocked_reason ? `原因：${c.blocked_reason}` : '未记录原因'}
                    {c.red_band_detail?.duration_minutes != null &&
                      ` · 已 ${Math.round(c.red_band_detail.duration_minutes / 60)} 小时`}
                    {c.depends_on.length > 0 && ` · 依赖 ${c.depends_on.length} 个前置`}
                    {c.red_band_detail?.escalated && ' · 已升级'}
                  </span>
                </div>
              ))}
          </section>
        )}

        {/* 视图切换：语义用 tablist/tab（e2e 曾因 role=tab vs button 翻车） */}
        <div className="kb-views" role="tablist" aria-label="看板视图切换">
          {VIEWS.map((v) => (
            <button
              key={v.id}
              type="button"
              role="tab"
              id={`kb-tab-${v.id}`}
              aria-selected={view === v.id}
              aria-controls={`kb-panel-${v.id}`}
              className="kb-tab"
              data-testid={`kb-tab-${v.id}`}
              onClick={() => setView(v.id)}
            >
              {v.label}
            </button>
          ))}
        </div>

        {view === 'board' && (
          <div
            className="kb-board"
            role="tabpanel"
            id="kb-panel-board"
            aria-labelledby="kb-tab-board"
            data-testid="kb-board"
          >
            {data.columns.map((column) => (
              <section
                key={column.id}
                className={`kb-col${dragOver === column.id ? ' kb-col--over' : ''}`}
                data-testid={`kb-col-${column.id}`}
                data-count={column.count}
                aria-label={COLUMN_META[column.id].label}
                onDragOver={(e) => {
                  e.preventDefault();
                  setDragOver(column.id);
                }}
                onDragLeave={() => setDragOver((c) => (c === column.id ? null : c))}
                onDrop={(e) => {
                  e.preventDefault();
                  void onDrop(column.id);
                }}
              >
                <h2 className="kb-col__head">
                  {COLUMN_META[column.id].label}
                  <span className="kb-col__count">{column.count}</span>
                </h2>
                <div className="kb-col__body">
                  {column.cards.length === 0 && (
                    <p className="kb-empty" data-testid={`kb-empty-${column.id}`}>
                      暂无任务
                    </p>
                  )}
                  {column.cards.map((card) => (
                    <KanbanCardView
                      key={card.id}
                      card={card}
                      busy={busy}
                      onOpen={() => void openDetail(card.id)}
                      onDragStart={() => {
                        dragId.current = card.id;
                      }}
                    />
                  ))}
                </div>
              </section>
            ))}
          </div>
        )}

        {view === 'list' && (
          <div
            className="kb-list"
            role="tabpanel"
            id="kb-panel-list"
            aria-labelledby="kb-tab-list"
            data-testid="kb-list"
          >
            <div className="kb-list__sort">
              <span className="ui-hint">排序</span>
              {(
                [
                  ['priority', '优先级'],
                  ['updated', '更新时间'],
                  ['deadline', '截止时间'],
                  ['progress', '进度'],
                ] as Array<[SortKey, string]>
              ).map(([key, label]) => (
                <button
                  key={key}
                  type="button"
                  className="kb-chip kb-chip--btn"
                  aria-pressed={sortKey === key}
                  data-testid={`kb-sort-${key}`}
                  onClick={() => setSortKey(key)}
                >
                  {label}
                </button>
              ))}
            </div>
            <table className="kb-table">
              <thead>
                <tr>
                  <th scope="col">任务</th>
                  <th scope="col">状态</th>
                  <th scope="col">进度</th>
                  <th scope="col">权重</th>
                  <th scope="col">子任务</th>
                  <th scope="col">截止</th>
                </tr>
              </thead>
              <tbody>
                {sorted.map((card) => (
                  <tr key={card.id} data-testid={`kb-row-${card.id}`}>
                    <td>
                      <button
                        type="button"
                        className="kb-linkbtn"
                        onClick={() => void openDetail(card.id)}
                      >
                        {card.goal}
                      </button>
                      {card.red_band && (
                        <span className="kb-tag kb-tag--red" data-testid={`kb-row-red-${card.id}`}>
                          红带
                        </span>
                      )}
                    </td>
                    <td>{card.status}</td>
                    <td>
                      {card.weighted_progress}%{' '}
                      <span className="ui-hint">
                        {card.progress_source === 'subtasks' ? '加权' : ''}
                      </span>
                    </td>
                    <td>{card.weight}</td>
                    <td>
                      {card.done_subtask_count}/{card.subtask_count}
                    </td>
                    <td>{card.deadline ? card.deadline.slice(0, 10) : '未设置'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}

        {view === 'timeline' && (
          <div
            className="kb-timeline"
            role="tabpanel"
            id="kb-panel-timeline"
            aria-labelledby="kb-tab-timeline"
            data-testid="kb-timeline"
          >
            <section aria-label="甘特图">
              <h2 className="kb-sub">甘特式时间线</h2>
              <GanttChart rows={buildGanttRows(cards)} />
            </section>
            <section aria-label="燃尽图">
              <h2 className="kb-sub">燃尽图</h2>
              {burn ? (
                <BurndownChart data={burn} />
              ) : (
                <p className="ui-hint">燃尽数据不可用</p>
              )}
            </section>
          </div>
        )}

        {detail && (
          <TaskDetailDrawer
            detail={detail}
            busy={busy}
            onClose={() => setDetail(null)}
            onOperate={(op) => void operate(detail.task.id, op)}
            onStart={() => void onStart(detail.task.id)}
            onSetProgress={(p) => {
              setBusy(true);
              void setProgress(detail.task.id, p)
                .then(() => openDetail(detail.task.id))
                .catch((e) => setError(kanbanErrorCode(e)))
                .finally(() => setBusy(false));
            }}
          />
        )}
      </div>
    </BaseBound>
  );
}

function KanbanCardView({
  card,
  busy,
  onOpen,
  onDragStart,
}: {
  card: KanbanCard;
  busy: boolean;
  onOpen: () => void;
  onDragStart: () => void;
}) {
  return (
    <article
      className={`kb-card${card.red_band ? ' kb-card--red' : ''}`}
      draggable={!busy}
      data-testid={`kb-card-${card.id}`}
      data-progress={card.weighted_progress}
      data-red-band={card.red_band ? 'true' : 'false'}
      onDragStart={onDragStart}
    >
      <div className="kb-card__top">
        <button type="button" className="kb-linkbtn" onClick={onOpen}>
          {card.goal}
        </button>
        {card.critical && (
          <span className="kb-tag" data-testid={`kb-critical-${card.id}`}>
            关键
          </span>
        )}
      </div>
      <div className="kb-card__mid">
        <ProgressDonut
          percent={card.weighted_progress}
          tone={donutToneFor(card)}
          label={card.goal}
        />
        <div className="kb-card__meta">
          <span className="kb-status" data-status={card.status}>
            {card.status}
          </span>
          {card.progress_source === 'subtasks' && (
            <span className="ui-hint">
              加权 · {card.done_subtask_count}/{card.subtask_count} 子任务
            </span>
          )}
          {card.depends_on.length > 0 && (
            <span className="ui-hint">前置 {card.depends_on.length}</span>
          )}
          {card.blocked_reason && (
            <span className="kb-card__blocked" data-testid={`kb-blocked-${card.id}`}>
              {card.blocked_reason}
            </span>
          )}
        </div>
      </div>
    </article>
  );
}

function TaskDetailDrawer({
  detail,
  busy,
  onClose,
  onOperate,
  onStart,
  onSetProgress,
}: {
  detail: TaskDetailResponse;
  busy: boolean;
  onClose: () => void;
  onOperate: (op: KanbanOperation) => void;
  onStart: () => void;
  onSetProgress: (patch: { progress_percent?: number }) => void;
}) {
  const { task } = detail;
  const allowed = new Set(
    task.status === 'queued'
      ? ['start', 'terminate']
      : task.status === 'running'
        ? ['pause', 'terminate']
        : task.status === 'waiting_input'
          ? ['resume', 'terminate']
          : [],
  );

  return (
    <aside
      className="kb-drawer"
      role="dialog"
      aria-modal="false"
      aria-label={`任务详情：${task.goal}`}
      data-testid="kb-detail"
    >
      <header className="kb-drawer__head">
        <h2 className="kb-drawer__title">{task.goal}</h2>
        <button type="button" className="ui-btn" onClick={onClose} data-testid="kb-detail-close">
          关闭
        </button>
      </header>

      <div className="kb-drawer__body">
        <section aria-label="进度">
          <h3 className="kb-sub">进度</h3>
          <div className="kb-drawer__progress">
            <ProgressDonut percent={task.weighted_progress} size={56} tone={donutToneFor(task)} />
            <div>
              <p className="ui-hint">
                {task.progress_source === 'subtasks' ? '按子任务权重加权' : '本任务自身进度'}
              </p>
              {task.subtask_count > 0 && (
                <label className="kb-field">
                  <span>标记完成度 %</span>
                  <input
                    type="number"
                    min={0}
                    max={100}
                    defaultValue={task.own_progress ?? 0}
                    disabled={busy}
                    data-testid="kb-detail-progress-input"
                    onBlur={(e) => {
                      const v = Number(e.currentTarget.value);
                      if (!Number.isNaN(v)) onSetProgress({ progress_percent: v });
                    }}
                  />
                </label>
              )}
            </div>
          </div>
        </section>

        <section aria-label="待办清单">
          <h3 className="kb-sub">待办清单（{detail.checklist.length}）</h3>
          {detail.checklist.length === 0 ? (
            <p className="ui-hint" data-testid="kb-detail-empty">
              还没有子任务
            </p>
          ) : (
            <ul className="kb-checklist" data-testid="kb-detail-checklist">
              {detail.checklist.map((item) => (
                <li key={item.id} data-done={item.done ? 'true' : 'false'}>
                  <span aria-hidden="true">{item.done ? '☑' : '☐'}</span>
                  <span>{item.goal}</span>
                  {item.critical && <span className="kb-tag">关键</span>}
                </li>
              ))}
            </ul>
          )}
        </section>

        {detail.critical_items.length > 0 && (
          <section aria-label="关键事项">
            <h3 className="kb-sub">关键事项</h3>
            <ul className="kb-checklist" data-testid="kb-detail-critical">
              {detail.critical_items.map((item) => (
                <li key={item.id}>{item.goal}</li>
              ))}
            </ul>
          </section>
        )}

        <section aria-label="依赖">
          <h3 className="kb-sub">依赖</h3>
          <p className="ui-hint" data-testid="kb-detail-blockedby">
            前置：
            {detail.dependencies.blocked_by.length
              ? detail.dependencies.blocked_by
                  .map((d) => `${d.task_id}${d.satisfied ? '（已满足）' : '（未完成）'}`)
                  .join('、')
              : '无'}
          </p>
          <p className="ui-hint" data-testid="kb-detail-blocks">
            挡着：
            {detail.dependencies.blocks.length
              ? detail.dependencies.blocks.map((d) => d.task_id).join('、')
              : '无'}
          </p>
        </section>

        <section aria-label="历史记录">
          <h3 className="kb-sub">历史记录（{detail.history.length}）</h3>
          {detail.history.length === 0 ? (
            <p className="ui-hint" data-testid="kb-detail-nohistory">
              还没有变更记录
            </p>
          ) : (
            <ol className="kb-history" data-testid="kb-detail-history">
              {detail.history.map((e) => (
                <li key={e.id}>
                  <code>{e.kind}</code>{' '}
                  {e.from_status && e.to_status ? `${e.from_status} → ${e.to_status}` : ''}
                  <span className="ui-hint"> {e.created_at?.slice(0, 19).replace('T', ' ')}</span>
                </li>
              ))}
            </ol>
          )}
        </section>
      </div>

      <footer className="kb-drawer__foot">
        <button
          type="button"
          className="ui-btn"
          disabled={busy || !allowed.has('start')}
          data-testid="kb-detail-start"
          onClick={onStart}
        >
          启动
        </button>
        <button
          type="button"
          className="ui-btn"
          disabled={busy || !allowed.has('pause')}
          data-testid="kb-detail-pause"
          onClick={() => onOperate('pause')}
        >
          暂停
        </button>
        <button
          type="button"
          className="ui-btn"
          disabled={busy || !allowed.has('resume')}
          data-testid="kb-detail-resume"
          onClick={() => onOperate('resume')}
        >
          恢复
        </button>
        <button
          type="button"
          className="ui-btn ui-btn--danger"
          disabled={busy || !allowed.has('terminate')}
          data-testid="kb-detail-terminate"
          onClick={() => onOperate('terminate')}
        >
          终止
        </button>
      </footer>
    </aside>
  );
}