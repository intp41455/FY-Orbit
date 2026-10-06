/**
 * 任务看板的三个纯 SVG 可视化组件（A-任务看板-03 / 12 / 13）。
 *
 * 🔴 **不引入任何图表库**（package.json 不加依赖）。理由不只是任务书的禁令：
 * 这三个图形都是「一个圆 / 几条线 / 几条矩形」，手写 SVG 不到 150 行，
 * 而一个图表库会带来几十到几百 KB 的前端体积，并且它的默认配色无法满足
 * 「零纯绿 + 只用 --ui-st-* 九档令牌」这条硬约束——最后通常还得覆写它的 CSS。
 *
 * 三条共同纪律：
 * 1. 颜色一律 `currentColor` 或 `var(--ui-st-*)`，**不写裸 hex/rgba**；
 * 2. `prefers-reduced-motion` 下没有动画（这里干脆完全不加 transition，
 *    由 CSS 兜底，不依赖 JS 读媒体查询）；
 * 3. 数值缺失时**诚实显示**——不画点、不连线、不补零，而不是画一条假线。
 */
import type { BurndownResponse, KanbanCard } from '../../api/kanban';

/* ------------------------------------------------------------------ */
/* 03 · 加权进度 Donut（卡片角落，中心留白放数字）                       */
/* ------------------------------------------------------------------ */

/**
 * 加权进度环形图。
 *
 * 用 `stroke-dasharray` 画弧：周长 C = 2πr，`dasharray = pct/100*C C`，
 * 再 `rotate(-90)` 让 0% 从 12 点方向起。中心留白放数字，符合需求 03
 * 「环形图在卡片角落，中心留白放数字，单一指标占比更紧凑」。
 */
export function ProgressDonut({
  percent,
  size = 44,
  stroke = 5,
  label,
  tone = 'var(--ui-st-running)',
}: {
  percent: number;
  size?: number;
  stroke?: number;
  label?: string;
  tone?: string;
}) {
  const clamped = Math.max(0, Math.min(100, Math.round(percent)));
  const r = (size - stroke) / 2;
  const c = 2 * Math.PI * r;
  // dash 段长按比例，gap 段补满剩余周长，最后一段闭合（0% 时不画弧）
  const dash = (clamped / 100) * c;

  return (
    <svg
      className="kb-donut"
      width={size}
      height={size}
      viewBox={`0 0 ${size} ${size}`}
      role="img"
      aria-label={`${label ?? '进度'} ${clamped}%`}
      data-testid="kb-donut"
      data-percent={clamped}
    >
      {/* 轨道：中性底，不承载状态语义 */}
      <circle
        cx={size / 2}
        cy={size / 2}
        r={r}
        fill="none"
        stroke="var(--ui-glass-line)"
        strokeWidth={stroke}
      />
      {clamped > 0 && (
        <circle
          cx={size / 2}
          cy={size / 2}
          r={r}
          fill="none"
          stroke={tone}
          strokeWidth={stroke}
          strokeLinecap="round"
          strokeDasharray={`${dash} ${c}`}
          transform={`rotate(-90 ${size / 2} ${size / 2})`}
        />
      )}
      <text
        className="kb-donut__num"
        x="50%"
        y="50%"
        dominantBaseline="central"
        textAnchor="middle"
        fontSize={size * 0.3}
      >
        {clamped}
      </text>
    </svg>
  );
}

/** 完成态用 --ui-st-complete，阻塞用 --ui-st-blocked。红带一律强红。 */
export function donutToneFor(card: KanbanCard): string {
  if (card.red_band) return 'var(--ui-st-failed)';
  if (card.status === 'completed') return 'var(--ui-st-complete)';
  if (card.status === 'failed') return 'var(--ui-st-failed)';
  if (card.status === 'waiting_input' || card.status === 'waiting_approval') {
    return 'var(--ui-st-blocked)';
  }
  return 'var(--ui-st-running)';
}

/* ------------------------------------------------------------------ */
/* 13 · 甘特式时间线                                                  */
/* ------------------------------------------------------------------ */

export interface GanttRow {
  card: KanbanCard;
  /** null = 未排期（诚实占位，不编造日期） */
  startMs: number | null;
  endMs: number | null;
}

/**
 * 把卡片摊成甘特行。
 *
 * 只有 `planned_start` 与 `planned_end` **都**存在才算已排期；只有一个也不算——
 * 甘特条画不出长度，编一个终点就是造假。未排期的行 startMs/endMs 为 null。
 */
export function buildGanttRows(cards: KanbanCard[]): GanttRow[] {
  return cards.map((card) => {
    if (!card.scheduled || !card.planned_start || !card.planned_end) {
      return { card, startMs: null, endMs: null };
    }
    const s = Date.parse(card.planned_start);
    const e = Date.parse(card.planned_end);
    if (Number.isNaN(s) || Number.isNaN(e) || e <= s) {
      return { card, startMs: null, endMs: null };
    }
    return { card, startMs: s, endMs: e };
  });
}

/**
 * 甘特图。行 = 顶层任务，条 = 起止时间。
 *
 * 未排期的行画一条虚线占位并标「未排期」——需求 13 要求看排期重叠，
 * 而没有排期的行如果静默消失，用户会以为板上没有这个任务。
 */
export function GanttChart({
  rows,
  width = 640,
  rowHeight = 30,
  labelWidth = 150,
}: {
  rows: GanttRow[];
  width?: number;
  rowHeight?: number;
  labelWidth?: number;
}) {
  const height = Math.max(rowHeight, rows.length * rowHeight + 8);
  const plotWidth = Math.max(40, width - labelWidth - 12);

  const scheduled = rows.filter((r) => r.startMs !== null && r.endMs !== null);
  const minMs = scheduled.length ? Math.min(...scheduled.map((r) => r.startMs as number)) : 0;
  const maxMs = scheduled.length ? Math.max(...scheduled.map((r) => r.endMs as number)) : 1;
  const span = Math.max(1, maxMs - minMs);

  return (
    <svg
      className="kb-gantt"
      viewBox={`0 0 ${width} ${height}`}
      width="100%"
      height={height}
      role="img"
      aria-label="甘特式时间线"
      data-testid="kb-gantt"
    >
      {rows.map((row, i) => {
        const y = i * rowHeight + 4;
        const tone = donutToneFor(row.card);
        const unscheduled = row.startMs === null || row.endMs === null;
        const x = unscheduled
          ? labelWidth
          : labelWidth + ((row.startMs as number) - minMs) / span * plotWidth;
        const barWidth = unscheduled
          ? plotWidth
          : Math.max(3, ((row.endMs as number) - (row.startMs as number)) / span * plotWidth);
        return (
          <g key={row.card.id} data-testid={`kb-gantt-row-${row.card.id}`}>
            <text x={0} y={y + rowHeight / 2} dominantBaseline="central" fontSize={12}>
              {row.card.goal.length > 18 ? `${row.card.goal.slice(0, 18)}…` : row.card.goal}
            </text>
            <rect
              x={x}
              y={y + 5}
              width={barWidth}
              height={rowHeight - 12}
              rx={4}
              fill={unscheduled ? 'none' : tone}
              stroke={unscheduled ? 'var(--ui-ink-4)' : 'none'}
              strokeWidth={unscheduled ? 1 : 0}
              strokeDasharray={unscheduled ? '4 3' : undefined}
              opacity={unscheduled ? 0.7 : 0.85}
            />
            {unscheduled && (
              <text
                x={x + 6}
                y={y + rowHeight / 2}
                dominantBaseline="central"
                fontSize={11}
                fill="var(--ui-ink-3)"
              >
                未排期
              </text>
            )}
          </g>
        );
      })}
    </svg>
  );
}

/* ------------------------------------------------------------------ */
/* 12 · 燃尽 / 燃起                                                   */
/* ------------------------------------------------------------------ */

/**
 * 燃尽图：理想线（虚线）+ 实际线（实线）。
 *
 * 🔴 诚实性是这里的全部难点：
 * - `partial=true` → 画一条「数据不足」提示，**不画实际线**；
 * - 未采样日（当天无完成事件）→ 断开，不把前值平移过去假装当天测过。
 *   实现上用 `sampled` 过滤后连线，段与段之间自然断开。
 */
export function BurndownChart({
  data,
  width = 640,
  height = 200,
}: {
  data: BurndownResponse;
  width?: number;
  height?: number;
}) {
  const padL = 34;
  const padB = 26;
  const padT = 10;
  const plotW = Math.max(40, width - padL - 10);
  const plotH = Math.max(40, height - padB - padT);
  const maxY = Math.max(1, data.initial_weight);

  const x = (i: number) =>
    padL + (data.days <= 1 ? 0 : (i / (data.days - 1)) * plotW);
  const y = (v: number) => padT + plotH - (v / maxY) * plotH;

  const idealPath = data.ideal
    .map((p, i) => `${i === 0 ? 'M' : 'L'}${x(i).toFixed(1)},${y(p.remaining_weight).toFixed(1)}`)
    .join(' ');

  // 只用采样日连线；未采样日不产生点，段与段之间自然断开。
  const sampledIdx = data.actual
    .map((p, i) => (p.sampled ? i : -1))
    .filter((i) => i >= 0);
  const actualPath = sampledIdx
    .map((k, n) => {
      const p = data.actual[k];
      return `${n === 0 ? 'M' : 'L'}${x(k).toFixed(1)},${y(p.remaining_weight).toFixed(1)}`;
    })
    .join(' ');

  return (
    <svg
      className="kb-burndown"
      viewBox={`0 0 ${width} ${height}`}
      width="100%"
      height={height}
      role="img"
      aria-label="燃尽图：理想线与实际线"
      data-testid="kb-burndown"
      data-partial={data.partial ? 'true' : 'false'}
      data-sampled-days={data.sampled_days}
    >
      {[0, 0.5, 1].map((f) => (
        <g key={f}>
          <line
            x1={padL}
            x2={padL + plotW}
            y1={y(maxY * f)}
            y2={y(maxY * f)}
            stroke="var(--ui-glass-line)"
            strokeWidth={1}
          />
          <text x={4} y={y(maxY * f)} dominantBaseline="central" fontSize={10} fill="var(--ui-ink-3)">
            {Math.round(maxY * f)}
          </text>
        </g>
      ))}
      {/* 理想线：虚线，斜坡参考 */}
      <path
        d={idealPath}
        fill="none"
        stroke="var(--ui-ink-4)"
        strokeWidth={1.5}
        strokeDasharray="5 4"
        data-testid="kb-burndown-ideal"
      />
      {data.partial || !actualPath ? (
        // 样本不足：不画实际线，只说明为什么没有
        <text
          x={padL + plotW / 2}
          y={padT + plotH / 2}
          textAnchor="middle"
          fontSize={12}
          fill="var(--ui-ink-3)"
          data-testid="kb-burndown-no-data"
        >
          数据不足：还没有完成事件可采样
        </text>
      ) : (
        <path
          d={actualPath}
          fill="none"
          stroke="var(--ui-st-running)"
          strokeWidth={2}
          data-testid="kb-burndown-actual"
        />
      )}
    </svg>
  );
}