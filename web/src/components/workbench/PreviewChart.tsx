/**
 * P2 · 四层预览 Layer 4 — 图像/图表渲染（纯代码 SVG）。
 *
 * 选型说明（工单授权自定）：**零外部图表库、零外部素材**——图表全部由
 * 本组件以确定性几何生成 React/SVG。与后端 `services/preview_sources.py`
 * 的 `build_render_spec` 解析规则逐字对齐（前端镜像用于即时渲染，
 * 后端注册协议仍是权威）。
 *
 * 诚实性规则：`parseRenderSpec` 对任何不可渲染输入抛 `ChartSpecError`
 * （带人类可读原因）——调用方（PreviewPane）必须显式显示该失败，
 * 绝不允许回落成空白预览冒充成功。
 */
import { useMemo } from 'react';

export type ChartType = 'bar' | 'line' | 'pie';

export interface ChartSeriesPoint {
  label: string;
  value: number;
}

export interface ChartSpec {
  chart: ChartType;
  title?: string;
  series: ChartSeriesPoint[];
}

/** 图表数据不可渲染：UI 必须显式呈现该失败（不许空白冒充成功）。 */
export class ChartSpecError extends Error {}

const CHART_TYPES: ChartType[] = ['bar', 'line', 'pie'];

function specFromJson(content: string): ChartSpec {
  let doc: unknown;
  try {
    doc = JSON.parse(content);
  } catch (e) {
    throw new ChartSpecError(`JSON 无法解析为图表数据: ${(e as Error).message}`);
  }
  if (typeof doc !== 'object' || doc === null || Array.isArray(doc)) {
    throw new ChartSpecError('JSON 图表数据必须是对象');
  }
  const obj = doc as Record<string, unknown>;
  const chart = obj.chart;
  if (typeof chart !== 'string' || !CHART_TYPES.includes(chart as ChartType)) {
    throw new ChartSpecError(`未知图型 ${JSON.stringify(chart)}（缺少合法的 chart 字段）；只支持 bar/line/pie`);
  }
  if (!Array.isArray(obj.series) || obj.series.length === 0) {
    throw new ChartSpecError('"series" 必须是非空数组（{label, value}）');
  }
  const series = (obj.series as unknown[]).map((item, i) => {
    if (typeof item !== 'object' || item === null || Array.isArray(item)) {
      throw new ChartSpecError(`series[${i}] 必须是 {label, value} 对象`);
    }
    const rec = item as Record<string, unknown>;
    if (!('label' in rec) || !('value' in rec)) {
      throw new ChartSpecError(`series[${i}] 缺少 label 或 value`);
    }
    const { label, value } = rec;
    if (typeof label !== 'string') {
      throw new ChartSpecError(`series[${i}].label 必须是字符串`);
    }
    // bool 是 number 的子类：true/false 不是可作图数值（与后端一致）。
    if (typeof value !== 'number' || Number.isNaN(value)) {
      throw new ChartSpecError(`series[${i}].value 必须是数值`);
    }
    return { label, value };
  });
  const spec: ChartSpec = { chart: chart as ChartType, series };
  if (obj.title !== undefined) {
    if (typeof obj.title !== 'string') throw new ChartSpecError('"title" 必须是字符串');
    spec.title = obj.title;
  }
  return spec;
}

function specFromCsv(content: string): ChartSpec {
  const rows = content
    .replace(/\r\n/g, '\n')
    .replace(/\r/g, '\n')
    .split('\n')
    .filter((r) => r.trim() !== '');
  if (rows.length < 2) {
    throw new ChartSpecError('CSV 图表数据至少需要表头行与一行数据');
  }
  const split = (line: string) => line.split(',');
  const header = split(rows[0]);
  if (header.length < 2) {
    throw new ChartSpecError('CSV 图表数据至少需要两列（标签, 数值）');
  }
  const series = rows.slice(1).map((line, i) => {
    const cells = split(line);
    if (cells.length < 2) {
      throw new ChartSpecError(`CSV 第 ${i + 2} 行不足两列`);
    }
    const value = Number(cells[1]);
    if (Number.isNaN(value)) {
      throw new ChartSpecError(`CSV 第 ${i + 2} 行第二列 ${JSON.stringify(cells[1])} 不是数值`);
    }
    return { label: cells[0], value };
  });
  return { chart: 'bar', series };
}

/** 把数据文件内容解析为图表规格（与后端 build_render_spec 规则对齐）。 */
export function parseRenderSpec(content: string, path: string): ChartSpec {
  const lower = path.toLowerCase();
  if (lower.endsWith('.json')) return specFromJson(content);
  if (lower.endsWith('.csv')) return specFromCsv(content);
  throw new ChartSpecError(`不支持的数据预览类型: ${path}`);
}

const W = 320;
const H = 200;
const PAD = 34;
const COLORS = ['#3a5f8a', '#2f6f4f', '#7a4a8a', '#b07d3a', '#8a3a52', '#3a7a7a'];

function BarChart({ spec }: { spec: ChartSpec }) {
  const max = Math.max(...spec.series.map((p) => Math.abs(p.value)), 1);
  const bw = (W - PAD * 2) / spec.series.length;
  const scale = (H - PAD * 2) / max;
  return (
    <g data-testid="chart-bar">
      {spec.series.map((p, i) => {
        const h = Math.abs(p.value) * scale;
        return (
          <rect
            key={i}
            x={PAD + i * bw + 4}
            y={H - PAD - h}
            width={Math.max(bw - 8, 2)}
            height={h}
            fill={COLORS[i % COLORS.length]}
          />
        );
      })}
      {spec.series.map((p, i) => (
        <text
          key={`l${i}`}
          x={PAD + i * bw + bw / 2}
          y={H - PAD + 12}
          fontSize={9}
          textAnchor="middle"
        >
          {p.label}
        </text>
      ))}
    </g>
  );
}

function LineChart({ spec }: { spec: ChartSpec }) {
  const max = Math.max(...spec.series.map((p) => Math.abs(p.value)), 1);
  const step = spec.series.length > 1 ? (W - PAD * 2) / (spec.series.length - 1) : 0;
  const scale = (H - PAD * 2) / max;
  const pts = spec.series.map((p, i) => ({
    x: PAD + i * step,
    y: H - PAD - Math.abs(p.value) * scale,
  }));
  const path = pts.map((pt) => `${pt.x},${pt.y}`).join(' ');
  return (
    <g data-testid="chart-line">
      <polyline points={path} fill="none" stroke="#3a5f8a" strokeWidth={2} />
      {pts.map((pt, i) => (
        <circle key={i} cx={pt.x} cy={pt.y} r={3} fill="#3a5f8a" />
      ))}
      {spec.series.map((p, i) => (
        <text key={`l${i}`} x={pts[i].x} y={H - PAD + 12} fontSize={9} textAnchor="middle">
          {p.label}
        </text>
      ))}
    </g>
  );
}

function PieChart({ spec }: { spec: ChartSpec }) {
  const total = spec.series.reduce((acc, p) => acc + Math.abs(p.value), 0) || 1;
  const cx = W / 2;
  const cy = H / 2;
  const r = Math.min(W, H) / 2 - PAD;
  let angle = -Math.PI / 2;
  const slices = spec.series.map((p, i) => {
    const frac = Math.abs(p.value) / total;
    const a2 = angle + frac * Math.PI * 2;
    const large = frac > 0.5 ? 1 : 0;
    const x1 = cx + r * Math.cos(angle);
    const y1 = cy + r * Math.sin(angle);
    const x2 = cx + r * Math.cos(a2);
    const y2 = cy + r * Math.sin(a2);
    const d = `M ${cx} ${cy} L ${x1} ${y1} A ${r} ${r} 0 ${large} 1 ${x2} ${y2} Z`;
    angle = a2;
    return { d, fill: COLORS[i % COLORS.length], label: p.label, x: cx + (r / 2) * Math.cos((angle + a2) / 2), y: cy + (r / 2) * Math.sin((angle + a2) / 2) };
  });
  return (
    <g data-testid="chart-pie">
      {slices.map((s, i) => (
        <path key={i} d={s.d} fill={s.fill} stroke="#fff" strokeWidth={1} />
      ))}
      {slices.map((s, i) => (
        <text key={`l${i}`} x={s.x} y={s.y} fontSize={9} fill="#fff" textAnchor="middle">
          {s.label}
        </text>
      ))}
    </g>
  );
}

/** Layer 4 图表渲染组件：确定性 SVG，零外部素材。 */
export function PreviewChart({ spec }: { spec: ChartSpec }) {
  const body = useMemo(() => {
    if (spec.chart === 'line') return <LineChart spec={spec} />;
    if (spec.chart === 'pie') return <PieChart spec={spec} />;
    return <BarChart spec={spec} />;
  }, [spec]);
  return (
    <div className="preview-chart" data-testid="wb-preview-chart">
      <svg
        data-testid="wb-preview-chart-svg"
        viewBox={`0 0 ${W} ${H}`}
        role="img"
        aria-label={`图表：${spec.title ?? spec.chart}，共 ${spec.series.length} 个数据点`}
      >
        {body}
      </svg>
      <div className="muted small" data-testid="wb-preview-chart-meta">
        {spec.title ?? '(无标题)'} · {spec.chart} · {spec.series.length} 点
      </div>
    </div>
  );
}
