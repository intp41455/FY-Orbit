/**
 * 包 E 私有组件 · 3D 知识星图渲染器（canvas 2D + 手写透视，无 three / 无 WebGL）
 * ---------------------------------------------------------------------------
 * 性能约束（包 E 任务书 §3.6 + 总纲 §6）：
 *  - **按需重绘**：rAF 循环只在「有动画或刚发生交互」时跑，空闲立刻停。
 *    常驻 rAF 空转会让笔记本风扇起飞，也会让 FPS 数字失去意义。
 *  - 生长动画：700ms，节点错开 ≤3ms，**参与错开的节点上限 24 个**，其余同时出现。
 *  - 呼吸：只给权重最高的 5 个节点，1.6s 缓慢明暗，**不位移**。
 *  - 拖拽节点：直接更新坐标，**不做弹性补间**（几百节点时补间会让帧率雪崩）。
 *  - 缩放/平移：滚轮缩放 0.4–3.0，空白处拖拽平移/旋转，`F` 适配全图。
 *  - prefers-reduced-motion：生长直接跳终态、关闭呼吸。
 *  - 视锥剔除：只画视口内 + 边距。
 */
import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import {
  BREATH_COUNT,
  MAX_ZOOM,
  MIN_ZOOM,
  STAGGER_LIMIT,
  clampPitch,
  clampZoom,
  centerOn,
  clusterColor,
  cull,
  defaultView,
  edgeWidth,
  fitAll,
  hitTest,
  nodeRadius,
  placeCard,
  project,
  weightNorm,
  type StarEdge,
  type StarNode,
  type ViewTransform,
} from './starLogic';

export interface StarGraphProps {
  nodes: StarNode[];
  edges: StarEdge[];
  selectedId: string | null;
  /** 查询词；非空时非命中节点降到弱化态 */
  filter: string;
  onSelect: (node: StarNode | null) => void;
  /** 过滤变化时把首个命中节点平移进视口 */
  focusFirstMatchToken: number;
  reducedMotion: boolean;
  height?: number;
  /** 节点卡内的动作按钮（由页面注入，因为它要跳页 / 打开原文件） */
  cardActions?: ReactNode;
}

/** CSS 变量 → 具体色值（canvas 不能用 var()，只在初始化时读一次）。 */
const CSS_VARS = [
  '--ui-ink-2',
  '--ui-ink-3',
  '--ui-ink-4',
  '--ui-line-1',
  '--ui-line-2',
  '--ui-line-strong',
  '--ui-glow-sky',
  '--ui-glow-teal',
  '--ui-sky-200',
  '--ui-sky-300',
  '--ui-sky-400',
  '--ui-sky-500',
  '--ui-sky-600',
  '--ui-teal-300',
  '--ui-violet-400',
  '--ui-violet-500',
  '--ui-mint-400',
  '--ui-sky-700',
  '--ui-glass-3',
  '--ui-ink-1',
] as const;

type Palette = Record<(typeof CSS_VARS)[number], string>;

function readPalette(): Palette {
  const cs = getComputedStyle(document.documentElement);
  const out = {} as Palette;
  for (const name of CSS_VARS) {
    out[name] = cs.getPropertyValue(name).trim();
  }
  // canvas 的 fillStyle/strokeStyle 不认 var()，所以必须拿到实色。
  // 若某个令牌读不到（理论上不会：tokens.css 是全局单一写入者且已落地），
  // 退回到「墨色四级灰」的实色 —— 仍然不写裸色字面量。
  const inkFallback = out['--ui-ink-4'];
  for (const name of CSS_VARS) {
    if (out[name].length === 0) out[name] = inkFallback;
  }
  return out;
}

/** 把 `#rrggbb` 换成 `rgba()`，用于画半透明。 */
function withAlpha(color: string, alpha: number): string {
  const c = color.trim();
  if (c.startsWith('#')) {
    const hex = c.length === 4
      ? c
          .slice(1)
          .split('')
          .map((ch) => ch + ch)
          .join('')
      : c.slice(1, 7);
    const n = parseInt(hex, 16);
    const r = (n >> 16) & 255;
    const g = (n >> 8) & 255;
    const b = n & 255;
    return `rgba(${r},${g},${b},${alpha})`;
  }
  if (c.startsWith('rgb(')) return c.replace('rgb(', 'rgba(').replace(')', `,${alpha})`);
  return c;
}

const GROW_MS = 700;
const STAGGER_MS = 3;
const BREATH_MS = 1600;

export function StarGraph({
  nodes,
  edges,
  selectedId,
  filter,
  onSelect,
  focusFirstMatchToken,
  reducedMotion,
  height,
  cardActions,
}: StarGraphProps) {
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const wrapRef = useRef<HTMLDivElement | null>(null);
  const paletteRef = useRef<Palette | null>(null);
  const viewRef = useRef<ViewTransform>(defaultView(800, 500));
  const rafRef = useRef<number | null>(null);
  const growStartRef = useRef<number>(0);
  /** 拖拽中的临时状态（存 ref 而不是 state，避免每帧 setState） */
  const dragRef = useRef<
    | { mode: 'none' }
    | { mode: 'rotate'; lastX: number; lastY: number }
    | { mode: 'pan'; lastX: number; lastY: number }
    | { mode: 'node'; id: string }
  >({ mode: 'none' });
  const movedRef = useRef(false);

  const [zoomLabel, setZoomLabel] = useState(1);
  const [hiddenCount, setHiddenCount] = useState(0);
  const [size, setSize] = useState({ w: 800, h: 500 });

  /* ---------------- 尺寸跟随 ----------------
     ⚠ 不在 render 里读布局（web-design-guidelines：render 阶段禁止布局读取）。
     ⚠ ResizeObserver 在 jsdom 里不存在（测试环境），必须降级而不是崩：
        与 ChatPage.tsx 对 IntersectionObserver 的处理一致，先 typeof 再用。
  */
  useEffect(() => {
    const el = wrapRef.current;
    if (!el) return;

    const measure = () => {
      const r = el.getBoundingClientRect();
      const w = Math.max(320, Math.round(r.width));
      const rawH = height ?? Math.round(r.height);
      const h = Math.max(280, rawH > 0 ? rawH : 500);
      setSize({ w, h });
      viewRef.current = { ...viewRef.current, width: w, height: h };
      requestDraw();
    };

    if (typeof ResizeObserver === 'undefined') {
      // 测试环境 / 老浏览器：退化成一次性测量 + window.resize
      measure();
      window.addEventListener('resize', measure);
      return () => window.removeEventListener('resize', measure);
    }

    const ro = new ResizeObserver(measure);
    ro.observe(el);
    measure();
    return () => ro.disconnect();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [height]);

  /* ---------------- 绘制 ---------------- */
  const draw = useCallback(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const palette = paletteRef.current ?? readPalette();
    paletteRef.current = palette;

    const dpr = Math.min(2, globalThis.devicePixelRatio || 1);
    if (canvas.width !== Math.round(size.w * dpr) || canvas.height !== Math.round(size.h * dpr)) {
      canvas.width = Math.round(size.w * dpr);
      canvas.height = Math.round(size.h * dpr);
    }
    const ctx = canvas.getContext('2d');
    if (!ctx) return;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);

    const v = viewRef.current;
    const now = performance.now();
    const growT = reducedMotion ? 1 : (now - growStartRef.current) / GROW_MS;

    ctx.clearRect(0, 0, size.w, size.h);

    /* ---- 背景：深底 + 极淡网格（≤2 层渐变，控制 overdraw） ---- */
    const bg = ctx.createLinearGradient(0, 0, size.w, size.h);
    bg.addColorStop(0, withAlpha(palette['--ui-sky-200'], 0.5));
    bg.addColorStop(1, withAlpha(palette['--ui-violet-400'], 0.22));
    ctx.fillStyle = bg;
    ctx.fillRect(0, 0, size.w, size.h);

    /* ---- 视锥剔除 ---- */
    const { visible: visNodes, hidden } = cull(nodes, v, 56);
    const visIds = new Set(visNodes.map((n) => n.id));
    const maxWeight = nodes.reduce((m, n) => Math.max(m, n.weight), 1);
    const filtering = filter.trim().length > 0;

    /* ---- 呼吸节点：只取权重最高的 5 个 ---- */
    const breathIds = new Set<string>();
    if (!reducedMotion && growT >= 1) {
      const top = [...nodes].sort((a, b) => b.weight - a.weight).slice(0, BREATH_COUNT);
      for (const n of top) breathIds.add(n.id);
    }

    /* ---- 连线 ---- */
    ctx.lineCap = 'round';
    ctx.lineJoin = 'round';
    for (const e of edges) {
      if (!visIds.has(e.source) || !visIds.has(e.target)) continue;
      const s = nodes.find((n) => n.id === e.source);
      const t = nodes.find((n) => n.id === e.target);
      if (!s || !t) continue;
      const a = project(s, v);
      const b = project(t, v);
      if (!a.visible || !b.visible) continue;

      const endpointMatched = s.matched && t.matched;
      let alpha = e.kind === 'sequence' ? 0.3 : 0.5;
      if (filtering && !endpointMatched) alpha = 0.12;
      const selected = selectedId === s.id || selectedId === t.id;
      if (selected) alpha = Math.min(1, alpha + 0.4);

      // 线型即语义（基准原文：不能只用颜色区分连线）
      ctx.setLineDash(
        e.kind === 'sequence' ? [5, 4] : e.kind === 'cohit' ? [] : [2, 3],
      );
      ctx.lineWidth = edgeWidth(e.strength) * Math.min(a.scale, 2);
      ctx.strokeStyle = selected
        ? withAlpha(palette['--ui-sky-600'], 0.95)
        : withAlpha(palette['--ui-line-strong'], alpha);
      ctx.beginPath();
      ctx.moveTo(a.sx, a.sy);
      ctx.lineTo(b.sx, b.sy);
      ctx.stroke();
    }
    ctx.setLineDash([]);

    /* ---- 节点 ---- */
    // 生长动画：前 STAGGER_LIMIT 个节点按索引错开，其余同时出现
    const staggerIdx = new Map<string, number>();
    for (let i = 0; i < Math.min(nodes.length, STAGGER_LIMIT); i += 1) {
      staggerIdx.set(nodes[i].id, i);
    }

    for (const n of visNodes) {
      const p = project(n, v);
      if (!p.visible) continue;

      // 生长进度
      let g = 1;
      if (growT < 1) {
        const si = staggerIdx.get(n.id);
        const delay = si === undefined ? STAGGER_LIMIT * STAGGER_MS : si * STAGGER_MS;
        g = Math.max(0, Math.min(1, (growT * GROW_MS - delay) / (GROW_MS * 0.6)));
      }
      if (g <= 0) continue;

      // 命中过滤弱化
      let dim = 1;
      if (filtering && !n.matched) dim = 0.22;

      // 呼吸（只明暗，不位移）
      let breath = 1;
      if (breathIds.has(n.id)) {
        const phase = ((now % BREATH_MS) / BREATH_MS) * Math.PI * 2;
        breath = 0.82 + 0.18 * (0.5 + 0.5 * Math.sin(phase));
      }

      const r = nodeRadius(n.weight) * p.scale * g;
      if (r <= 0.2) continue;

      const color = clusterColor(n.cluster);
      const wn = weightNorm(n.weight, maxWeight);
      // 亮度随权重：sky-200 → sky-600 的色相跨度由 alpha + 描边宽度表达
      ctx.globalAlpha = dim * breath * g;

      const isSelected = n.id === selectedId;
      if (isSelected) {
        ctx.beginPath();
        ctx.arc(p.sx, p.sy, r + 7, 0, Math.PI * 2);
        ctx.fillStyle = withAlpha(palette['--ui-glow-sky'], 0.55);
        ctx.fill();
      }

      ctx.beginPath();
      ctx.arc(p.sx, p.sy, r, 0, Math.PI * 2);
      ctx.fillStyle = color;
      ctx.fill();

      // 权重越高描边越亮（亮度第二通道：半径 + 描边，不只靠色相）
      ctx.lineWidth = (isSelected ? 3 : 1 + wn * 1.6) * Math.min(p.scale, 2);
      ctx.strokeStyle = withAlpha(
        wn > 0.66 ? palette['--ui-sky-700'] : palette['--ui-ink-2'],
        isSelected ? 0.95 : 0.35 + wn * 0.4,
      );
      ctx.stroke();

      // 检索命中：外环（形状通道，不只靠颜色）
      if (n.matched) {
        ctx.beginPath();
        ctx.arc(p.sx, p.sy, r + 4, 0, Math.PI * 2);
        ctx.lineWidth = 1.5;
        ctx.strokeStyle = withAlpha(palette['--ui-ink-1'], 0.55);
        ctx.stroke();
      }

      ctx.globalAlpha = 1;
    }

    setHiddenCount(hidden);
  }, [nodes, edges, selectedId, filter, size, reducedMotion]);

  /* ---------------- 按需重绘循环 ---------------- */
  const requestDraw = useCallback(() => {
    if (rafRef.current !== null) return;
    const loop = () => {
      rafRef.current = null;
      draw();
      const needsMore =
        !reducedMotion && performance.now() - growStartRef.current < GROW_MS + BREATH_MS * 2;
      if (needsMore) rafRef.current = requestAnimationFrame(loop);
    };
    rafRef.current = requestAnimationFrame(loop);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [draw, reducedMotion]);

  useEffect(() => {
    requestDraw();
  }, [draw, requestDraw]);

  useEffect(
    () => () => {
      if (rafRef.current !== null) cancelAnimationFrame(rafRef.current);
    },
    [],
  );

  /* ---------------- 过滤变化 → 首个命中节点自动平移进视口 ---------------- */
  useEffect(() => {
    if (focusFirstMatchToken === 0) return;
    const first = nodes.find((n) => n.matched) ?? nodes[0];
    if (!first) return;
    const p = project(first, { ...viewRef.current, panX: 0, panY: 0 });
    if (!p.visible) return;
    viewRef.current = {
      ...viewRef.current,
      panX: viewRef.current.panX + (viewRef.current.width / 2 - p.sx) / viewRef.current.zoom,
      panY: viewRef.current.panY + (viewRef.current.height / 2 - p.sy) / viewRef.current.zoom,
    };
    requestDraw();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [focusFirstMatchToken, nodes]);

  /* ---------------- 指针交互 ---------------- */
  const onPointerDown = (e: React.PointerEvent<HTMLCanvasElement>) => {
    const rect = e.currentTarget.getBoundingClientRect();
    const sx = e.clientX - rect.left;
    const sy = e.clientY - rect.top;
    movedRef.current = false;
    const hit = hitTest(nodes, viewRef.current, sx, sy);
    if (hit) {
      dragRef.current = { mode: 'node', id: hit.id };
      e.currentTarget.setPointerCapture(e.pointerId);
      return;
    }
    dragRef.current = e.shiftKey
      ? { mode: 'pan', lastX: sx, lastY: sy }
      : { mode: 'rotate', lastX: sx, lastY: sy };
    e.currentTarget.setPointerCapture(e.pointerId);
  };

  const onPointerMove = (e: React.PointerEvent<HTMLCanvasElement>) => {
    const d = dragRef.current;
    if (d.mode === 'none') return;
    const rect = e.currentTarget.getBoundingClientRect();
    const sx = e.clientX - rect.left;
    const sy = e.clientY - rect.top;
    movedRef.current = true;

    if (d.mode === 'rotate') {
      const v = viewRef.current;
      viewRef.current = {
        ...v,
        yaw: v.yaw + (sx - d.lastX) * 0.006,
        pitch: clampPitch(v.pitch + (sy - d.lastY) * 0.006),
      };
      d.lastX = sx;
      d.lastY = sy;
    } else if (d.mode === 'pan') {
      const v = viewRef.current;
      viewRef.current = {
        ...v,
        panX: v.panX + (sx - d.lastX) / v.zoom,
        panY: v.panY + (sy - d.lastY) / v.zoom,
      };
      d.lastX = sx;
      d.lastY = sy;
    } else if (d.mode === 'node') {
      // 直接更新，不做补间（任务书 §3.6）
      const v = viewRef.current;
      const n = nodes.find((x) => x.id === d.id);
      if (!n) return;
      const radius = Math.min(1.2, 0.3 + v.zoom * 0.1);
      const angX = ((sx - v.width / 2 - v.panX) / (v.width * 0.42)) / v.zoom;
      const angY = ((sy - v.height / 2 - v.panY) / (v.height * 0.42)) / v.zoom;
      n.x = angX * radius * 1.6;
      n.y = -angY * radius * 1.6;
      n.z = radius * 0.5;
    }
    requestDraw();
  };

  const onPointerUp = (e: React.PointerEvent<HTMLCanvasElement>) => {
    const d = dragRef.current;
    if (d.mode === 'node' && !movedRef.current) {
      const node = nodes.find((n) => n.id === d.id) ?? null;
      onSelect(node);
    } else if (d.mode === 'none') {
      onSelect(null);
    }
    dragRef.current = { mode: 'none' };
    if (e.currentTarget.hasPointerCapture(e.pointerId)) {
      e.currentTarget.releasePointerCapture(e.pointerId);
    }
  };

  /** 滚轮缩放：0.4–3.0（任务书 §3.6）。监听在非 passive 上以便 preventDefault。 */
  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const onWheel = (e: WheelEvent) => {
      e.preventDefault();
      const v = viewRef.current;
      const next = clampZoom(v.zoom * (e.deltaY < 0 ? 1.12 : 1 / 1.12));
      if (next !== v.zoom) {
        viewRef.current = { ...v, zoom: next };
        setZoomLabel(Number(next.toFixed(2)));
        requestDraw();
      }
    };
    canvas.addEventListener('wheel', onWheel, { passive: false });
    return () => canvas.removeEventListener('wheel', onWheel);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [requestDraw]);

  /* ---------------- 键盘：F 适配全图 / +/- 缩放 / Esc 取消选中 ---------------
     ⚠ canvas 里的节点对读屏不可见，所以除了 aria-label 之外，
       键盘用户必须有一条**真实可用**的选中路径 —— 否则节点卡上的
       「打开原文件 / 基于此内容对话」两个动作就是指针独占。
       做法：Tab 进入画布后用 ← → / ↑ ↓ 在「当前检索命中节点」之间移动，
       Enter/Space 选中。这样键盘用户也能走完「选中 → 看摘要 → 去对话」全链路。 */
  const focusIndexRef = useRef(0);
  const kbSelectable = useMemo(
    () => (filter.trim() ? nodes.filter((n) => n.matched) : nodes).slice(0, 200),
    [nodes, filter],
  );

  const moveKeyboardFocus = useCallback(
    (delta: number) => {
      if (kbSelectable.length === 0) return;
      const next = (focusIndexRef.current + delta + kbSelectable.length) % kbSelectable.length;
      focusIndexRef.current = next;
      const node = kbSelectable[next];
      onSelect(node);
      // 同步把该节点平移进视口，键盘用户不用自己找
      viewRef.current = centerOn(node, viewRef.current);
      requestDraw();
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [kbSelectable, onSelect],
  );

  const onKeyDown = (e: React.KeyboardEvent<HTMLDivElement>) => {
    if (e.key === 'f' || e.key === 'F') {
      viewRef.current = fitAll(nodes, viewRef.current);
      setZoomLabel(Number(viewRef.current.zoom.toFixed(2)));
      requestDraw();
    } else if (e.key === '+' || e.key === '=') {
      viewRef.current = { ...viewRef.current, zoom: clampZoom(viewRef.current.zoom * 1.15) };
      setZoomLabel(Number(viewRef.current.zoom.toFixed(2)));
      requestDraw();
    } else if (e.key === '-') {
      viewRef.current = { ...viewRef.current, zoom: clampZoom(viewRef.current.zoom / 1.15) };
      setZoomLabel(Number(viewRef.current.zoom.toFixed(2)));
      requestDraw();
    } else if (e.key === 'ArrowRight' || e.key === 'ArrowDown') {
      e.preventDefault();
      moveKeyboardFocus(1);
    } else if (e.key === 'ArrowLeft' || e.key === 'ArrowUp') {
      e.preventDefault();
      moveKeyboardFocus(-1);
    } else if (e.key === 'Enter' || e.key === ' ') {
      e.preventDefault();
      const node = kbSelectable[focusIndexRef.current];
      if (node) onSelect(node);
    } else if (e.key === 'Escape') {
      onSelect(null);
    }
  };

  const doZoom = (delta: number) => {
    const v = viewRef.current;
    viewRef.current = { ...v, zoom: clampZoom(v.zoom + delta) };
    setZoomLabel(Number(viewRef.current.zoom.toFixed(2)));
    requestDraw();
  };

  const doFit = () => {
    viewRef.current = fitAll(nodes, viewRef.current);
    setZoomLabel(Number(viewRef.current.zoom.toFixed(2)));
    requestDraw();
  };

  /* ---------------- 选中节点的卡片定位 ---------------- */
  const selected = selectedId ? nodes.find((n) => n.id === selectedId) ?? null : null;
  const cardPos = selected ? placeCard(project(selected, viewRef.current), viewRef.current) : null;

  /* ---------------- 重新生长（数据变化时） ---------------- */
  useEffect(() => {
    growStartRef.current = reducedMotion ? performance.now() - GROW_MS * 2 : performance.now();
    requestDraw();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [nodes.length]);

  return (
    <div
      ref={wrapRef}
      className="kn-stage"
      style={height ? { height } : undefined}
      onKeyDown={onKeyDown}
      tabIndex={0}
      role="group"
      aria-label="知识星图。方向键在节点间移动并选中，Enter 或空格打开节点卡，加减号缩放，F 适配全图，Esc 取消选中。等价内容见列表视图。"
    >
      <canvas
        ref={canvasRef}
        data-testid="kn-star-canvas"
        onPointerDown={onPointerDown}
        onPointerMove={onPointerMove}
        onPointerUp={onPointerUp}
        onPointerCancel={onPointerUp}
      />

      <div className="kn-stage-bar">
        <div className="kn-stage-tools">
          <button
            type="button"
            className="kn-stage-tool"
            aria-label="放大"
            data-testid="kn-zoom-in"
            onClick={() => doZoom(0.2)}
            disabled={zoomLabel >= MAX_ZOOM}
          >
            +
          </button>
          <button
            type="button"
            className="kn-stage-tool"
            aria-label="缩小"
            data-testid="kn-zoom-out"
            onClick={() => doZoom(-0.2)}
            disabled={zoomLabel <= MIN_ZOOM}
          >
            −
          </button>
          <button
            type="button"
            className="kn-stage-tool"
            onClick={doFit}
            data-testid="kn-fit"
            title="适配全图（F）"
          >
            <span className="ui-kbd" aria-hidden="true">
              F
            </span>
            适配
          </button>
        </div>

        <div className="kn-stage-bar-right">
          <span className="kn-count" data-testid="kn-zoom-label">
            缩放 {zoomLabel.toFixed(2)}
          </span>
          {hiddenCount > 0 && (
            <span className="kn-count" data-testid="kn-culled">
              视口外剔除 {hiddenCount}
            </span>
          )}
        </div>
      </div>

      {cardPos && selected && (
        <div
          className="kn-nodecard"
          data-testid="kn-nodecard"
          style={{ left: cardPos.left, top: cardPos.top }}
        >
          <div className="kn-nodecard-title">
            <span>{selected.docName}</span>
          </div>
          <div className="kn-nodecard-meta">
            第 {selected.seq + 1} 段 · {selected.chars} 字 · 权重 {selected.weight}
            {selected.score !== null && ` · 得分 ${selected.score.toFixed(2)}`}
          </div>
          <p className="kn-nodecard-body">{selected.excerpt || '（该切片正文为空）'}</p>
          {cardActions && <div className="kn-nodecard-actions">{cardActions}</div>}
        </div>
      )}
    </div>
  );
}